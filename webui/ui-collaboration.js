/* Durable collaboration tools. All DOM text goes through el()/textContent. */
const Collaboration = {
  request(method, path, body) {
    return _req(method, path, body);
  },
  async action(button, work) {
    button.disabled = true;
    try { await work(); }
    catch (e) { toast("Action Failed", e.message, "error"); }
    finally {
      button.disabled = false;
      // A dialog inside `work` may have tried to return focus while this
      // button was still disabled; reclaim it so keyboard flow continues.
      if (button.isConnected && (!document.activeElement || document.activeElement === document.body)) {
        button.focus();
      }
    }
  },
  button(label, work) {
    const b = el("button", { class: "btn", type: "button" }, label);
    b.onclick = () => Collaboration.action(b, work);
    return b;
  },
  field(label, input) { return el("label", { class: "collab-field" }, el("span", {}, label), input); },
  input(placeholder, multiline = false) {
    return el(multiline ? "textarea" : "input", { placeholder, ...(multiline ? {rows: 3} : {type: "text"}) });
  },
  async inbox() {
    const pid = AC.state.pid, hash = location.hash;
    const wrap = el("div", {class: "page narrow"}, el("h1", {}, "Reply Requests"),
      el("p", {class: "muted"}, "Delivery, acknowledgment, and resolution are separate. Reading a mention does not resolve a request."));
    const list = el("div", {class: "collab-list"});
    const target = el("select", {}, AC.state.agents.filter(a => !a.revoked).map(a => el("option", {value: a.alias}, a.alias)));
    const question = Collaboration.input("What do you need the agent to answer?", true);
    const send = Collaboration.button("Request Reply", async () => {
      if (!question.value.trim() || !target.value) throw new Error("Choose an agent and enter your question.");
      const main = AC.state.channels.find(c => c.is_main);
      if (!main) throw new Error("Main channel is unavailable.");
      await API.postMessage(pid, main.id, {body: `@${target.value} ${question.value}`, requires_ack: true});
      question.value = ""; await load(true);
    });
    wrap.append(el("div", {class: "card"}, Collaboration.field("Agent", target), Collaboration.field("Question", question), send), list);
    const more = Collaboration.button("Load More", () => load(false)); wrap.append(more);
    AC.setView(wrap);
    let before, generation=0;
    async function load(reset) {
      const currentGeneration=++generation;
      const data = await Collaboration.request("GET", `/api/projects/${pid}/inbox?outstanding=false${!reset && before ? `&before_id=${before}` : ""}`);
      if (location.hash !== hash || generation!==currentGeneration || !wrap.isConnected) return;
      if (reset) list.replaceChildren();
      for (const r of data.items) {
        const source = r.message_id ? `#/p/${pid}/c/${r.conversation_id}/m/${r.message_id}` : `#/p/${pid}/tickets/${r.ticket_number}`;
        const card = el("div", {class: "card"}, el("div", {class: "spread"},
          el("b", {}, `Request ${r.id} · ${r.state}`), el("a", {href: source}, "Open Source")),
          el("p", {}, r.body), el("p", {class: "muted small"},
          `From ${r.issuer_alias} · ${r.client_heartbeat_at ? "Client heartbeat " + fmtTime(r.client_heartbeat_at) : "No client heartbeat"} · ${r.delivered_at ? "Delivery reported" : "Delivery unconfirmed"} · ${fmtTime(r.created_at)}`));
        if (["pending", "acknowledged"].includes(r.state)) card.append(Collaboration.button("Cancel Request", async () => {
          const reason = await reasonModal("Cancel Request", "This request will no longer expect a reply.");
          if (!reason) return;
          await Collaboration.request("POST", `/api/projects/${pid}/inbox/${r.id}/cancel`, {reason}); await load(true);
        }));
        list.append(card);
      }
      before = data.next_before_id; more.hidden = !data.has_more;
      if (!list.children.length) list.append(el("p", {class: "muted"}, "No reply requests yet."));
    }
    await load(true);
    AC.on("inbox_requested", () => load(true), "inbox-new");
    AC.on("inbox_updated", () => load(true), "inbox-update");
  },
};
Views.inbox = Collaboration.inbox;

Views.context = async function () {
  const pid = AC.state.pid, hash = location.hash;
  const ticket = Collaboration.input("Optional ticket number"); ticket.type = "number"; ticket.min = "1";
  const result = el("div", {class: "collab-list"});
  const load = async () => {
    const data = await Collaboration.request("GET", `/api/projects/${pid}/context${ticket.value ? `?ticket_number=${Number(ticket.value)}` : ""}`);
    if (location.hash !== hash) return;
    result.replaceChildren(el("p", {class: "muted"}, data.truncated ? "Some context was omitted to fit the size limit. Open the original records for more." : "Current context with source references."));
    for (const section of data.sections) for (const item of section.items) {
      const card = el("div", {class: "card"}, el("h3", {}, section.kind.replaceAll("_", " ")),
        el("p", {}, item.title || item.purpose || item.agent_alias || ""),
        el("p", {}, item.body || item.description || item.completed || ""));
      if (item.next_action) card.append(el("p", {}, `Next: ${item.next_action}`));
      if (item.url) card.append(el("a", {href: item.url, target: "_blank", rel: "noopener"}, "Open Source"));
      for (const source of item.sources || []) card.append(el("p", {},
        el("a", {href: source.url, target: "_blank", rel: "noopener"}, `${source.kind} ${source.id}`),
        source.changed ? " · changed since checkpoint" : " · referenced revision"));
      result.append(card);
    }
  };
  AC.setView(el("div", {class: "page narrow"}, el("h1", {}, "Resume Context"),
    Collaboration.field("Ticket", ticket), Collaboration.button("Load Context", load), result));
  await load();
};

Collaboration.ticketControls = async function (pid, ticket) {
  const root = el("div", {class: "card", style: "margin-top:16px"}, el("h2", {}, "Coordination"));
  const base = `/api/projects/${pid}/tickets/${ticket.number}`;
  const deps = await Collaboration.request("GET", base + "/dependencies");
  const refresh = () => Views.ticketDetail(ticket.number);
  const save = async edges => {
    await Collaboration.request("PUT", base + "/dependencies", {expected_revision: ticket.revision, dependencies: edges});
    await refresh();
  };
  const edges = deps.items.map(d => ({prerequisite_number: d.prerequisite_number, waived: !!d.waived, waiver_reason: d.waiver_reason}));
  root.append(el("p", {class: "muted"}, ticket.managed_claim ? `Managed claim · Generation ${ticket.claim_generation}` : "Legacy assignment"));
  for (const d of edges) root.append(el("div", {class: "collab-actions"},
    el("a", {href: `#/p/${pid}/tickets/${d.prerequisite_number}`}, `Requires #${d.prerequisite_number}${d.waived ? " (Waived)" : ""}`),
    Collaboration.button("Remove Prerequisite", async () => {
      if (await modal({title:"Remove Prerequisite?", confirmText:"Remove Prerequisite", danger:true, body:el("p", {}, "This ticket will no longer depend on #" + d.prerequisite_number + ".")})) await save(edges.filter(e => e.prerequisite_number !== d.prerequisite_number));
    }),
    Collaboration.button(d.waived ? "Require Again" : "Waive", async () => {
      const reason = d.waived ? "" : await reasonModal("Waive Prerequisite", "This ticket will no longer wait for the prerequisite to finish.");
      if (!d.waived && !reason) return;
      await save(edges.map(e => e === d ? {...e, waived: !d.waived, waiver_reason: reason} : e));
    })));
  const number = Collaboration.input("Prerequisite ticket number"); number.type = "number"; number.min = "1";
  root.append(Collaboration.field("Add prerequisite", number), Collaboration.button("Add Dependency", () => {
    if (!Number(number.value)) throw new Error("Enter a ticket number.");
    return save([...edges, {prerequisite_number: Number(number.value), waived: false, waiver_reason: ""}]);
  }));
  if (ticket.blockers && ticket.blockers.length) root.append(el("p", {class: "muted"}, "Waiting on " + ticket.blockers.map(b => `#${b.prerequisite_number}`).join(", ")));
  if (ticket.managed_claim) {
    const target = el("select", {}, AC.state.agents.filter(a => !a.revoked && a.id !== ticket.owner_agent_id).map(a => el("option", {value: a.id}, a.alias)));
    const checkpoints = await Collaboration.request("GET", `/api/projects/${pid}/checkpoints?limit=200`);
    const cp = el("select", {}, checkpoints.items.filter(c => c.shared && c.ticket_number === ticket.number).map(c => el("option", {value: c.id}, `${c.id}: ${c.next_action}`)));
    root.append(Collaboration.field("Transfer to", target), Collaboration.field("Shared handoff checkpoint", cp),
      Collaboration.button("Offer Transfer", async () => {
        if (!cp.value || !target.value) throw new Error("The agent must create a shared checkpoint for this ticket first.");
        const reason = await reasonModal("Offer Transfer", "Ownership changes only after the recipient accepts.", false); if (!reason) return;
        await Collaboration.request("POST", base + "/handoffs", {expected_revision: ticket.revision, claim_generation: ticket.claim_generation,
          to_agent_id: Number(target.value), checkpoint_id: Number(cp.value), reason});
        toast("Transfer Offered", "The recipient must accept before ownership changes.");
      }));
  }
  if (ticket.assignee) root.append(Collaboration.button("Release Assignment", async () => {
    const reason = await reasonModal("Release Assignment", "Confirm the old runner has stopped editing before another agent takes over."); if (!reason) return;
    await Collaboration.request("POST", base + "/release", {expected_revision: ticket.revision, claim_generation: ticket.claim_generation, reason});
    await refresh();
  }));
  return root;
};

Views.decisionQueue = async function () {
  const pid = AC.state.pid, hash = location.hash;
  const list = el('div', {class: 'collab-list'});
  const question = Collaboration.input('What needs a human decision?', true);
  const options = Collaboration.input('One option per line', true);
  const recommendation = Collaboration.input('Recommended option and why', true);
  const ticket = Collaboration.input('Optional ticket number'); ticket.type = 'number';
  const load = async () => {
    const data = await Collaboration.request('GET', `/api/projects/${pid}/decision-requests?limit=200`);
    if (location.hash !== hash) return;
    list.replaceChildren();
    for (const d of data.items) {
      const card = el('div', {class:'card'}, el('h3', {}, `${d.question} · ${d.state}`),
        el('ul', {}, d.options.map(o => el('li', {}, o))), el('p', {}, d.recommendation));
      if (d.state === 'pending') {
        const result = Collaboration.input('Your decision'), reason = Collaboration.input('Reason', true);
        const resolve = async cancel => {
          if (!result.value.trim() || !reason.value.trim()) throw new Error('Enter a decision and reason.');
          if (cancel && !await modal({title:"Cancel Decision Request?", confirmText:"Cancel Request", danger:true, body:el("p", {}, "This decision request will be closed without a ruling.")})) return;
          await Collaboration.request('POST', `/api/projects/${pid}/decision-requests/${d.id}/resolve`, {expected_revision:d.revision, result:result.value, reason:reason.value, cancel});
          await load();
        };
        card.append(Collaboration.field('Decision', result), Collaboration.field('Reason', reason),
          Collaboration.button('Resolve', () => resolve(false)), Collaboration.button('Cancel', () => resolve(true)));
      } else card.append(el('p', {}, `${d.result}: ${d.reason}`));
      list.append(card);
    }
    if (!data.items.length) list.append(el('p', {class:'muted'}, 'No decisions requested.'));
    if (data.has_more) list.append(el('p', {class:'muted'}, 'Showing the latest 200 requests. Older records remain available through the paginated API.'));
  };
  AC.setView(el('div', {class:'page narrow'}, el('h1', {}, 'Decision Queue'),
    el('p', {class:'muted'}, 'Requests and answers are shared within this project. Resolutions are announced in the main channel.'),
    el('div', {class:'card'}, Collaboration.field('Question', question), Collaboration.field('Options', options),
      Collaboration.field('Recommendation', recommendation), Collaboration.field('Ticket', ticket),
      Collaboration.button('Request Decision', async () => {
        await Collaboration.request('POST', `/api/projects/${pid}/decision-requests`, {question:question.value, options:options.value.split('\n').filter(x=>x.trim()), recommendation:recommendation.value, ticket_number:ticket.value ? Number(ticket.value) : null});
        question.value=''; options.value=''; await load();
      })), list));
  await load();
};

Views.attention = async function () {
  const pid=AC.state.pid, hash=location.hash, list=el('div',{class:'collab-list'});
  const all=el('input',{type:'checkbox'}); let offset=0;
  const more=Collaboration.button('Load More',()=>load(false));
  const load=async reset=>{
    if(reset) offset=0;
    const data=await Collaboration.request('GET',`/api/admin/attention?offset=${offset}${all.checked?'':`&project_id=${pid}`}`);
    if(location.hash!==hash) return;
    if(reset) list.replaceChildren(el('p',{class:'muted'},`${data.total} items need attention.`));
    for(const item of data.items) {
      const route=item.route;
      list.append(el('a',{class:'card',href:`#/p/${item.project_id}/${route}`},
        el('b',{},`${item.kind.replaceAll('_',' ')} · Project ${item.project_id}`),el('p',{},item.title),el('span',{class:'muted'},fmtTime(item.created_at))));
    }
    offset=data.next_offset; more.hidden=offset===null;
  };
  all.onchange=()=>Collaboration.action(more,()=>load(true));
  AC.setView(el('div',{class:'page narrow'},el('h1',{},'Attention'),Collaboration.field('Across all projects',all),
    Collaboration.button('Refresh',()=>load(true)),list,more)); await load(true);
  const focus=()=>{if(location.hash===hash)load(true).catch(()=>{});};
  window.addEventListener('focus',focus);
  const poll=()=>{if(location.hash!==hash){window.removeEventListener('focus',focus);return;}
    if(document.visibilityState==='visible')load(true).catch(()=>{});
    setTimeout(poll,15000);};
  setTimeout(poll,15000);
};

Collaboration.evidenceControls=async function(pid,ticket){
  const base=`/api/projects/${pid}/tickets/${ticket.number}`, data=await Collaboration.request('GET',base+'/evidence');
  const root=el('div',{class:'card'},el('h2',{},'Completion Evidence'),el('p',{class:'muted'},'Evidence is reported by its submitter. Review the source and results before approving.'));
  const refresh=()=>Views.ticketDetail(ticket.number), policy=data.policy;
  const workspaces=await Collaboration.request('GET',`/api/projects/${pid}/workspaces`);
  const workspace=el('select',{},el('option',{value:''},'No workspace binding'),workspaces.items.map(w=>el('option',{value:w.id},w.name)));
  workspace.value=policy.workspace_id===null||policy.workspace_id===undefined?'':String(policy.workspace_id);
  const commit=Collaboration.input('Full source commit'); commit.value=policy.source_commit;
  const dirty=Collaboration.input('Optional SHA-256 of dirty worktree'); dirty.value=policy.worktree_fingerprint;
  const human=el('input',{type:'checkbox'});human.checked=!!policy.human_review;
  const kinds=['test','review','artifact'].map(kind=>{const input=el('input',{type:'checkbox'});input.checked=policy.required_kinds.includes(kind);return {kind,input};});
  root.append(el('details',{},el('summary',{},'Completion Policy'),Collaboration.field('Workspace',workspace),Collaboration.field('Source commit',commit),Collaboration.field('Dirty worktree fingerprint',dirty),
    ...kinds.map(k=>Collaboration.field(`Require ${k.kind}`,k.input)),Collaboration.field('Require human review',human),
    Collaboration.button('Save Policy',async()=>{await Collaboration.request('PUT',base+'/completion-policy',{expected_revision:ticket.revision,workspace_id:workspace.value?Number(workspace.value):null,required_kinds:kinds.filter(k=>k.input.checked).map(k=>k.kind),human_review:human.checked,source_commit:commit.value,worktree_fingerprint:dirty.value});await refresh();})));
  for(const e of data.items)root.append(el('div',{class:'card'},el('b',{},`${e.kind} · ${e.outcome} · ${e.provenance}`),el('p',{},e.summary),el('p',{class:'muted'},`${e.submitted_by} · ${e.source_commit.slice(0,12)} · ${fmtTime(e.created_at)}`),e.command?el('code',{},e.command):null));
  const summary=Collaboration.input('Evidence summary',true),command=Collaboration.input('Test command'),exit=Collaboration.input('Exit code');exit.type='number';
  const kind=el('select',{},['test','review'].map(k=>el('option',{value:k},chromeValue(k)))),outcome=el('select',{},['pass','fail','unknown'].map(k=>el('option',{value:k},chromeValue(k))));
  root.append(el('details',{},el('summary',{},'Record Test Or Review Evidence'),Collaboration.field('Kind',kind),Collaboration.field('Outcome',outcome),Collaboration.field('Summary',summary),Collaboration.field('Command',command),Collaboration.field('Exit code',exit),
    el('p',{class:'muted'},'Uses the source commit and worktree fingerprint entered above. Artifact evidence can be submitted through the API with an uploaded attachment.'),
    Collaboration.button('Record Evidence',async()=>{await Collaboration.request('POST',base+'/evidence',{workspace_id:workspace.value?Number(workspace.value):null,kind:kind.value,outcome:outcome.value,summary:summary.value,command:command.value,exit_code:exit.value===''?null:Number(exit.value),source_commit:commit.value,worktree_fingerprint:dirty.value});await refresh();})));
  root.append(Collaboration.button('Request Completion Review',async()=>{await Collaboration.request('POST',base+'/completion-proposals',{expected_revision:ticket.revision,claim_generation:ticket.claim_generation});await refresh();}));
  for(const p of data.proposals){
    const card=el('div',{class:'card'},el('b',{},`Review ${p.id} · ${p.state}${p.state==='pending'&&p.stale?' · Stale':''}`),el('p',{},p.reason||'Awaiting human review'));
    if(p.state==='pending') for(const approve of [true,false])card.append(Collaboration.button(approve?'Approve and Complete':'Reject',async()=>{const reason=await reasonModal(approve ? 'Approve and Complete' : 'Reject Completion', 'Record your review findings and reason.', !approve);if(!reason)return;await Collaboration.request('POST',`/api/projects/${pid}/completion-proposals/${p.id}/review`,{approve,reason});await refresh();}));
    root.append(card);
  }
  return root;
};

Views.workspaces = async function () {
  const pid=AC.state.pid, hash=location.hash;
  const root=Collaboration.input('Absolute path to an existing checkout'), name=Collaboration.input('Workspace name');
  const list=el('div',{class:'collab-list'}), reservations=el('div',{class:'collab-list'});
  const load=async()=>{
    const [data,claims]=await Promise.all([Collaboration.request('GET',`/api/projects/${pid}/workspaces`),Collaboration.request('GET',`/api/projects/${pid}/reservations?limit=200`)]);
    if(location.hash!==hash)return;
    list.replaceChildren(); reservations.replaceChildren(el('h2',{},'Active Reservations'));
    for(const w of data.items)list.append(el('div',{class:'card'},el('h3',{},w.name),el('p',{},w.root),el('p',{class:'muted'},`Workspace ${w.id} · ${w.watch_active?'Monitor active':w.watch_registered_at?'Monitor registered, inactive':'Monitor off'}`),
      Collaboration.button(w.watch_registered_at?'Stop Monitor':'Start Monitor',async()=>{await Collaboration.request('PUT',`/api/projects/${pid}/workspaces/${w.id}/watch`,{enabled:!w.watch_registered_at});await load();})));
    for(const r of claims.items){
      const target=el('select',{},AC.state.agents.filter(a=>!a.revoked&&a.id!==r.owner_agent_id).map(a=>el('option',{value:a.id},a.alias)));
      const change=async transfer=>{const reason=await reasonModal(transfer ? 'Transfer Reservation' : 'Release Reservation', 'Confirm the prior runner has stopped editing.');if(!reason)return;await Collaboration.request('POST',`/api/projects/${pid}/reservations/${r.id}/update`,{expected_revision:r.revision,to_agent_id:transfer?Number(target.value):null,reason});await load();};
      reservations.append(el('div',{class:'card'},el('b',{},`${r.path}${r.is_directory?'/':''} · ${r.owner_alias}`),el('p',{},r.reason),el('p',{class:'muted'},`Workspace ${r.workspace_id}`),target,Collaboration.button('Transfer',()=>change(true)),Collaboration.button('Release',()=>change(false))));
    }
    if(!claims.items.length)reservations.append(el('p',{class:'muted'},'No active reservations. Agents reserve paths before editing through the client API.'));
    if(claims.has_more)reservations.append(el('p',{class:'muted'},'Showing 200 active reservations; the API provides older pages.'));
  };
  AC.setView(el('div',{class:'page narrow'},el('h1',{},'Workspaces'),el('p',{class:'muted'},'Register existing checkouts. Monitors observe files; reservations coordinate agents and do not lock the filesystem.'),
    el('div',{class:'card'},Collaboration.field('Name',name),Collaboration.field('Checkout path',root),Collaboration.button('Register Workspace',async()=>{await Collaboration.request('POST',`/api/projects/${pid}/workspaces`,{name:name.value,root:root.value});name.value='';root.value='';await load();})),list,reservations));await load();
};
