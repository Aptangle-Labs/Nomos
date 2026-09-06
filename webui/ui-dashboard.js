/* Project operations dashboard. Fixed categorical order; no chart libraries.
   Current-state tiles and range-scoped history are deliberately labeled apart. */
(() => {
  "use strict";
  const SERIES = ["messages", "tickets", "files", "other"];
  const COLORS = SERIES.map((_, i) => `var(--chart-${i + 1})`);
  const STATUS = {open: "var(--text-dim)", "in-progress": "var(--text-faint)",
    "awaiting-human": "var(--amber)", blocked: "var(--danger)", done: "var(--ok)", wontfix: "var(--line-bright)"};
  const EMPTY = "No activity in this range. Widen the range or check back after the team starts.";
  const settings = new Map();
  const svg = (tag, attrs = {}, text) => {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const number = n => n == null ? "no data yet" : Number(n).toLocaleString();
  const duration = n => n == null ? "no data yet" : n < 60 ? `${Math.round(n)}s` : n < 3600 ? `${Math.round(n / 60)}m` : `${(n / 3600).toFixed(1)}h`;
  const chip = color => el("span", {class: "dash-chip", style: `background:${color}`});
  const empty = () => el("p", {class: "dash-empty"}, EMPTY);
  function table(headers, rows) {
    return el("div", {class: "dash-table-scroll", tabindex: "0"}, el("table", {class: "dash-table"},
      el("thead", {}, el("tr", {}, headers.map(h => el("th", {scope: "col"}, h)))),
      el("tbody", {}, rows.map(row => el("tr", {}, row.map(v => el("td", {}, v == null ? "no data yet" : String(v))))))));
  }
  // Square baseline, 4px rounded data end; vertical bars round only the top.
  function barPath(x, y, w, h, vertical = false) {
    const r = Math.min(4, w / 2, h / 2);
    return vertical ? `M${x},${y+h}V${y+r}Q${x},${y} ${x+r},${y}H${x+w-r}Q${x+w},${y} ${x+w},${y+r}V${y+h}Z`
      : `M${x},${y}H${x+w-r}Q${x+w},${y} ${x+w},${y+r}V${y+h-r}Q${x+w},${y+h} ${x+w-r},${y+h}H${x}Z`;
  }

  Views.projectDashboard = async function () {
    if (!AC.state.adminAlias) {
      AC.setView(el("div", {class: "page"}, "Dashboard is available to the admin session."));
      return;
    }
    const pid = AC.state.pid, generation = AC._busGen;
    const saved = settings.get(pid) || {range: "7d", tables: new Set()};
    settings.set(pid, saved);
    let data = null, request = 0, timer = null, pendingSince = 0, disposed = false, lastWidth = 0;
    const status = el("span", {class: "t-chrome faint", role: "status", "aria-live": "polite"});
    const stamp = el("span", {class: "t-chrome faint"});
    const grid = el("div", {class: "dash-grid"});
    const tiles = el("div", {class: "dash-tiles"});
    const attention = el("div", {class: "dash-attention hidden"});
    const tooltip = el("div", {class: "dash-tooltip hidden", role: "tooltip", id: "dashboard-tooltip"});
    const rangeButtons = ["24h", "7d", "30d", "all"].map(range => el("button", {
      class: "dash-range", type: "button", "aria-pressed": String(saved.range === range),
      title: range === "all" ? "All-history totals; charts show at most the latest 90 daily buckets. Times are UTC."
        : `Rolling ${range} history; current-state tiles stay current. Times are UTC.`,
      onclick: () => {
        if (saved.range === range) return;
        saved.range = range; data = null; tooltip.classList.add("hidden");
        grid.replaceChildren(el("p", {class: "dash-empty"}, "Loading dashboard…"));
        tiles.replaceChildren(); attention.classList.add("hidden"); stamp.textContent = "";
        syncRange(); load();
      },
    }, range === "all" ? "All" : range));
    function syncRange() {
      rangeButtons.forEach((button, i) => button.setAttribute("aria-pressed", String(["24h", "7d", "30d", "all"][i] === saved.range)));
    }
    syncRange();
    const retry = el("button", {class: "btn small", onclick: () => load()}, "Refresh");
    const page = el("div", {class: "page dash-page"},
      el("div", {class: "spread"}, el("h1", {class: "page-title"}, "Dashboard"), retry),
      el("div", {class: "dash-toolbar"}, el("div", {class: "dash-ranges", role: "group", "aria-label": "Dashboard range"}, rangeButtons), stamp),
      status, attention, tiles, grid, tooltip);
    AC.setView(page);
    const alive = () => !disposed && AC.state.pid === pid && AC._busGen === generation;
    function showTip(target, heading, rows, ev) {
      tooltip.replaceChildren(el("div", {class: "t-label"}, heading), ...rows.map(([label, value, color]) =>
        el("div", {class: "dash-tip-row"}, color ? chip(color) : null, el("span", {}, label), el("b", {}, String(value)))));
      tooltip.classList.remove("hidden");
      const bounds = target.getBoundingClientRect();
      const x = ev && ev.clientX != null ? ev.clientX : bounds.left + bounds.width / 2;
      const y = ev && ev.clientY != null ? ev.clientY : bounds.top;
      tooltip.style.left = `${Math.max(8, Math.min(x + 12, window.innerWidth - tooltip.offsetWidth - 8))}px`;
      tooltip.style.top = `${Math.max(8, Math.min(y + 12, window.innerHeight - tooltip.offsetHeight - 8))}px`;
    }
    function hit(target, heading, rows) {
      target.setAttribute("tabindex", "0");
      target.setAttribute("aria-label", `${heading}: ${rows.map(r => `${r[0]} ${r[1]}`).join(", ")}`);
      target.setAttribute("aria-describedby", tooltip.id);
      target.addEventListener("pointermove", ev => showTip(target, heading, rows, ev));
      target.addEventListener("focus", () => showTip(target, heading, rows));
      for (const event of ["pointerleave", "blur"]) target.addEventListener(event, () => tooltip.classList.add("hidden"));
      target.addEventListener("keydown", ev => { if (ev.key === "Escape") tooltip.classList.add("hidden"); });
    }
    function card(key, title, span, headers, rows, draw, subtitle = "") {
      const body = el("div", {class: "dash-card-body"});
      const toggle = el("button", {class: "btn small", "aria-pressed": String(saved.tables.has(key)),
        "aria-label": `Show table for ${title}`, onclick: () => {
          saved.tables.has(key) ? saved.tables.delete(key) : saved.tables.add(key);
          toggle.setAttribute("aria-pressed", String(saved.tables.has(key)));
          tooltip.classList.add("hidden"); paint();
        }}, "Table");
      const node = el("section", {class: `card dash-card dash-span-${span}`, "aria-label": title},
        el("div", {class: "spread"}, el("h2", {class: "t-label"}, title), toggle),
        subtitle ? el("p", {class: "t-chrome faint dash-subtitle"}, subtitle) : null, body);
      function paint() {
        body.replaceChildren(saved.tables.has(key) ? table(headers, rows) : draw(Math.max(240, node.clientWidth - 40)));
      }
      grid.append(node); paint();
    }
    function makeSvg(width, height, title) {
      const node = svg("svg", {viewBox: `0 0 ${width} ${height}`, width, height, class: "dash-svg", role: "group", "aria-label": title});
      node.append(svg("title", {}, title)); return node;
    }
    function axes(node, width, height, max, left = 32, right = 12) {
      for (let i = 0; i <= 3; i++) {
        const y = 16 + (height - 44) * i / 3;
        node.append(svg("line", {x1: left, x2: width-right, y1: y, y2: y, class: "dash-gridline"}),
          svg("text", {x: left-6, y: y+3, "text-anchor": "end", class: "dash-axis"}, Math.round(max*(1-i/3))));
      }
    }
    function lineChart(width) {
      const points = data.activity_series.points;
      if (!points.some(p => SERIES.some(s => p[s] > 0))) return empty();
      const height = 244, left = 32, right = 84, top = 16, bottom = 200;
      const max = Math.max(1, ...points.flatMap(p => SERIES.map(s => p[s])));
      const x = i => left + (width-left-right)*i/Math.max(1,points.length-1);
      const y = n => bottom - n/max*(bottom-top);
      const node = makeSvg(width, height, "Activity by UTC bucket; use arrow keys to inspect");
      axes(node, width, 228, max, left, right);
      SERIES.forEach((series, i) => {
        node.append(svg("polyline", {points: points.map((p,j) => `${x(j)},${y(p[series])}`).join(" "), fill: "none", stroke: COLORS[i], "stroke-width": 2}));
        if (points.length === 1) node.append(svg("circle", {cx:x(0), cy:y(points[0][series]), r:3, fill:COLORS[i]}));
      });
      const labels = SERIES.map((series,i) => ({series,i,y:y(points[points.length-1][series])})).sort((a,b) => a.y-b.y);
      labels.forEach((label,i) => { label.pos = Math.max(label.y, i ? labels[i-1].pos+16 : top); });
      const excess = Math.max(0, labels[labels.length-1].pos-bottom);
      labels.forEach(label => {
        const pos=label.pos-excess;
        node.append(svg("line", {x1:width-right,y1:label.y,x2:width-right+8,y2:pos,stroke:COLORS[label.i],"stroke-width":1}),
          svg("text", {x:width-right+12,y:pos+4,class:"dash-direct"}, label.series));
      });
      [0, Math.floor((points.length-1)/2), points.length-1].filter((v,i,a) => a.indexOf(v)===i).forEach(i => {
        node.append(svg("text", {x:x(i),y:228,"text-anchor":i===0?"start":i===points.length-1?"end":"middle",class:"dash-axis"},
          data.activity_series.bucket === "hour" ? points[i].t.slice(11,16) : points[i].t.slice(5)));
      });
      const cross = svg("line", {y1:top,y2:bottom,class:"dash-crosshair",visibility:"hidden"});
      const overlay = svg("rect", {x:left,y:0,width:width-left-right,height:height,fill:"transparent",tabindex:0,
        "aria-label":"Activity chart; left and right arrows select a UTC bucket", "aria-describedby":tooltip.id});
      let selected=points.length-1;
      function inspect(i,ev) {
        selected=Math.max(0,Math.min(points.length-1,i)); const p=points[selected];
        cross.setAttribute("x1",x(selected));cross.setAttribute("x2",x(selected));cross.setAttribute("visibility","visible");
        const rows=SERIES.map((s,j) => [s,number(p[s]),COLORS[j]]);
        overlay.setAttribute("aria-label", `${p.t}: ${rows.map(r => `${r[0]} ${r[1]}`).join(", ")}`);
        showTip(overlay,`${p.t} UTC`,rows,ev);
      }
      overlay.addEventListener("pointermove",ev => {
        const rect=node.getBoundingClientRect();
        inspect(Math.round(((ev.clientX-rect.left)*width/rect.width-left)/(width-left-right)*(points.length-1)),ev);
      });
      overlay.addEventListener("focus",() => inspect(selected));
      overlay.addEventListener("keydown",ev => {
        if (["ArrowLeft","ArrowRight","Home","End"].includes(ev.key)) {
          ev.preventDefault(); inspect(ev.key==="Home"?0:ev.key==="End"?points.length-1:selected+(ev.key==="ArrowRight"?1:-1));
        } else if(ev.key==="Escape") hide();
      });
      function hide() {cross.setAttribute("visibility","hidden");tooltip.classList.add("hidden");}
      overlay.addEventListener("pointerleave",hide);overlay.addEventListener("blur",hide);
      node.append(cross,overlay);
      return el("div", {}, node, el("div", {class:"dash-legend"}, SERIES.map((s,i) => el("span",{},chip(COLORS[i]),s))));
    }
    function segments(width, entries, title) {
      const total = entries.reduce((sum,e) => sum+e[1],0);
      if (!total) return empty();
      const node = makeSvg(width, 32, title), legend = el("div", {class:"dash-legend dash-legend-list"});
      let x=0;
      entries.forEach(([label,value,color]) => {
        const segmentWidth=value/total*width;
        const rows=[[label,`${number(value)} · ${(value/total*100).toFixed(1)}%`,color]];
        if(value) {
          node.append(svg("path", {d:barPath(x,8,Math.max(0,segmentWidth-2),12),fill:color}));
          const target=svg("rect",{x:Math.max(0,Math.min(x,width-24)),y:0,width:Math.max(24,segmentWidth),height:32,fill:"transparent"});
          hit(target,title,rows); node.append(target);
        }
        const labelNode=el("div",{class:"dash-segment-label"},chip(color),el("span",{},label),el("b",{},number(value)));
        hit(labelNode,title,rows); legend.append(labelNode); x+=segmentWidth;
      });
      return el("div",{},node,legend);
    }
    function agentBars(width) {
      if(!data.top_agents.length) return empty();
      const max=Math.max(...data.top_agents.map(a => a.total));
      return el("div",{class:"dash-agents"},data.top_agents.map(a => {
        const w=Math.max(64,width-100), node=makeSvg(w,28,`${a.alias}: ${a.total}`);
        const length=a.total/max*(w-42);
        node.append(svg("path",{d:barPath(0,8,length,10),fill:"var(--text-dim)"}),
          svg("text",{x:length+6,y:19,class:"dash-direct"},number(a.total)));
        const row=el("div",{class:"dash-agent"},el("span",{class:"dash-agent-label",title:a.alias},AC.avatarEl(a,20),el("span",{},a.alias)),node);
        hit(row,a.alias,[["messages",number(a.messages)],["ticket transitions",number(a.tickets)],["audit reports",number(a.audit)],["total",number(a.total)]]);
        return row;
      }));
    }
    function completionBars(width) {
      const points=data.completions_series;
      const bars=el("div",{class:"dash-chart-scroll",tabindex:0,"aria-label":"Completions chart; scroll for more buckets"});
      if(!points.some(p => p.done)) bars.append(empty());
      else {
        // Each bar gets its own 24px target even when 90 buckets need scrolling.
        const w=Math.max(width,points.length*24+48), h=180, left=32, plot=w-44;
        const node=makeSvg(w,h,"Completions Over Time in UTC");
        const max=Math.max(1,...points.map(p => p.done));
        axes(node,w,h,max);
        points.forEach((p,i) => {
          const step=plot/points.length,x=left+i*step,barHeight=p.done/max*(h-44);
          if(p.done) node.append(svg("path",{d:barPath(x+4,h-28-barHeight,Math.min(12,step-8),barHeight,true),fill:COLORS[0]}));
          const target=svg("rect",{x,y:0,width:step,height:h-20,fill:"transparent"});
          hit(target,`${p.t} UTC`,[["done transitions",number(p.done),COLORS[0]]]); node.append(target);
          if(i===0||i===points.length-1||i%Math.ceil(points.length/6)===0) node.append(svg("text",{
            x:x+4,y:h-4,class:"dash-axis", "text-anchor":i===points.length-1?"end":"start"},
            data.activity_series.bucket==="hour"?p.t.slice(11,16):p.t.slice(5)));
        });
        bars.append(node);
      }
      const g=data.gates, rate=g.first_review_acceptance_rate;
      const values=[["Evidence Passed",g.evidence_pass],["Evidence Failed",g.evidence_fail],
        ["Reviews Approved",g.proposals_approved],["Reviews Rejected",g.proposals_rejected],
        ["First Review Acceptance",rate==null?"no data yet":`${Math.round(rate*100)}% · n=${g.first_review_samples}`]];
      return el("div",{},bars,el("div",{class:"dash-gates"},values.map(([label,value]) =>
        el("div",{},el("strong",{},typeof value==="number"?number(value):value),el("span",{class:"t-chrome faint"},label)))));
    }
    function latencyTiles() {
      const l=data.latency;
      if(!["ack","resolve","decision_wait"].some(k => l[`${k}_samples`])) return el("div",{},el("p",{class:"t-chrome faint"},"no data yet"),empty());
      return el("div",{class:"dash-latency"},[["ack","Acknowledgment"],["resolve","Resolution"],["decision_wait","Decision Wait"]].map(([key,label]) =>
        el("div",{},el("span",{class:"t-label"},label),el("strong",{},duration(l[`${key}_median_s`])),
          el("span",{class:"t-chrome faint"},`p90 ${duration(l[`${key}_p90_s`])} · n=${l[`${key}_samples`]}`))));
    }
    function render() {
      if(!data||!alive()) return;
      tooltip.classList.add("hidden");
      const t=data.tiles, a=data.attention;
      stamp.textContent=`as of ${new Date(data.as_of).toLocaleTimeString()} · buckets UTC`;
      attention.replaceChildren();
      const links=[[a.tickets_awaiting_human,"tickets awaiting human","tickets"],
        [a.proposals_pending,"completion reviews pending","attention"],
        [a.reservation_conflicts,"reservation conflicts in range","workspaces"]];
      links.filter(([count]) => count>0).forEach(([count,label,route]) =>
        attention.append(el("a",{href:`#/p/${pid}/${route}`},`${number(count)} ${label}`)));
      attention.classList.toggle("hidden",!links.some(([n]) => n>0));
      const tileRows=[
        [number(t.agents_online),"Agents Online",`${t.agents_total} current · ${t.agents_ever} ever · ${t.sessions_open} open sessions`,"agents",false],
        [number(t.tickets_open),"Open Tickets Now","All unfinished statuses","tickets",false],
        [number(t.tickets_done_range),"Done in Range","Transitions to done","tickets",false],
        [number(t.reply_requests_outstanding),"Awaiting Replies Now","Outstanding reply requests","inbox",t.reply_requests_outstanding>0],
        [number(t.decisions_pending),"Pending Decisions Now","Open decision requests","decisionQueue",t.decisions_pending>0],
        [t.audit_chain_ok==null?"no data yet":t.audit_chain_ok?"Verified":"Divergence","Audit Chain",`${number(t.audit_records)} retained records`,"audit",t.audit_chain_ok===false],
      ];
      tiles.replaceChildren(...tileRows.map(([value,label,detail,route,warn]) => el("a",{
        class:`dash-tile${warn?" dash-warn":""}`,href:`#/p/${pid}/${route}`},el("strong",{class:"t-display"},value),
        el("span",{class:"t-label"},label),el("span",{class:"t-chrome faint"},detail))));
      grid.replaceChildren();
      card("activity","Activity Over Time",8,["UTC Bucket",...SERIES],data.activity_series.points.map(p => [p.t,...SERIES.map(s => p[s])]),lineChart,
        "Messages · ticket events · file work · other work");
      const states=Object.entries(data.tickets_by_status);
      card("status","Tickets by Status",4,["Status","Count"],states.map(([s,n]) => [chromeValue(s),n]),w => segments(w,states.map(([s,n]) => [chromeValue(s),n,STATUS[s]||"var(--text-faint)"]),"Tickets by Status"),"Current state");
      const attribution=[["Correlated",data.attribution.correlated,"var(--ok)"],["Self-Reported Only",data.attribution.self_reported_only,"var(--text-dim)"],["Monitor Only",data.attribution.monitor_only,"var(--amber)"]];
      card("attribution","Attribution",4,["Source","Count"],attribution.map(r => r.slice(0,2)),w => segments(w,attribution,"Attribution"),"Observed pairs and unmatched records in range");
      card("agents","Most Active Agents",4,["Agent","Messages","Ticket Transitions","Audit","Total"],data.top_agents.map(a => [a.alias,a.messages,a.tickets,a.audit,a.total]),agentBars,"Activity volume, not productivity · top 8");
      card("latency","Response Latency",4,["Measure","Median Seconds","p90 Seconds","Samples"],
        ["ack","resolve","decision_wait"].map(k => [k,data.latency[`${k}_median_s`],data.latency[`${k}_p90_s`],data.latency[`${k}_samples`]]),latencyTiles,
        "Completed responses in range · median / nearest-rank p90");
      card("completions","Completions Over Time",12,["UTC Bucket / Gate","Count / Rate"],
        [...data.completions_series.map(p => [p.t,p.done]),...Object.entries(data.gates)],completionBars,
        "Transitions to done, including repeat completions after reopening");
    }
    async function load() {
      if(!alive()) return;
      const token=++request, range=saved.range;
      status.textContent=data?"Refreshing…":"Loading dashboard…";
      try {
        const result=await _req("GET",`/api/projects/${pid}/dashboard?range=${range}`);
        if(!alive()||token!==request||range!==saved.range) return;
        data=result; status.textContent=""; render();
      } catch(error) {
        if(!alive()||token!==request) return;
        status.textContent=`${data?"Showing stale data. ":""}Dashboard refresh failed: ${error.message} Use Refresh to retry.`;
        if(!data) grid.replaceChildren(el("p",{class:"dash-empty"},"Dashboard unavailable. Use Refresh to try again."));
      }
    }
    const observer=new ResizeObserver(entries => {
      const width=entries[0].contentRect.width;
      if(Math.abs(width-lastWidth)>1) { lastWidth=width; render(); }
    });
    observer.observe(grid);
    function dispose() {
      pendingSince=0;
      disposed=true;request++;clearTimeout(timer);observer.disconnect();
      tooltip.classList.add("hidden");window.removeEventListener("hashchange",dispose);
    }
    window.addEventListener("hashchange",dispose,{once:true});
    ["message","message_edited","message_deleted","ticket_created","ticket_updated","ticket_deleted","ticket_comment",
      "audit","inbox_requested","inbox_updated","decision_requested","decision_resolved","completion_requested","completion_reviewed",
      "evidence_updated","policy_updated","agent_joined","agent_updated","agent_revoked","agent_removed","reservation_conflict","reservation_updated"].forEach(event => {
      AC.on(event,() => {
        // Trailing debounce with a ceiling: sustained event traffic must
        // not postpone the refetch forever.
        const nowT=Date.now();
        if(!pendingSince)pendingSince=nowT;
        clearTimeout(timer);
        timer=setTimeout(() => { pendingSince=0; load(); },Math.min(3000,Math.max(250,pendingSince+10000-nowT)));
      },`dashboard-${event}`);
    });
    await load();
  };
})();
