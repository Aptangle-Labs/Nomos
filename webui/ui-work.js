/* Views: kanban board (drag & drop) and tickets (list, detail, create). */
"use strict";

/* Small assignee chip: 16px avatar + alias. */
function assigneeChip(alias) {
  if (!alias) return null;
  return el("span", { class: "label-chip" }, AC.avatarEl(alias, 16), "@" + alias);
}

/* ---------- kanban ---------- */
Views.board = async function () {
  const pid = AC.state.pid;
  const data = await API.board(pid);
  const columns = data.columns || [];
  const wrap = el("div", { class: "board-wrap" });
  AC.setView(wrap);

  for (const col of columns) {
    const body = el("div", { class: "col-body" });
    const colEl = el("div", { class: "kanban-col" },
      el("div", { class: "col-head" },
        el("span", {}, col.name),
        el("span", { class: "faint" }, (col.statuses || []).map(chromeValue).join(" · ")),
        el("span", { class: "cnt" }, String((col.cards || []).length))),
      body);
    colEl.addEventListener("dragover", (e) => { e.preventDefault(); colEl.classList.add("dragover"); });
    colEl.addEventListener("dragleave", () => colEl.classList.remove("dragover"));
    colEl.addEventListener("drop", async (e) => {
      e.preventDefault();
      colEl.classList.remove("dragover");
      const n = Number(e.dataTransfer.getData("text/ticket-number"));
      if (!n) return;
      try {
        const card = columns.flatMap(c => c.cards || []).find(t => t.number === n);
        await API.moveCard(pid, n, col.id, card && card.revision);
        Views.board();
      } catch (err) { toast("Move Failed", err.message, "error"); }
    });
    for (const t of col.cards || []) body.append(kanbanCard(t));
    if (!(col.cards || []).length) body.append(el("div", {class:"col-empty"},
      el("p", {class:"muted"}, "No tickets in " + col.name + "."),
      el("button", {class:"btn small", onclick:() => Views.ticketNew()}, "Create Ticket")));
    wrap.append(colEl);
  }

  function kanbanCard(t) {
    const card = el("div", { class: "kcard", draggable: "true", onclick: () => location.hash = `#/p/${pid}/tickets/${t.number}` },
      el("div", { class: "knum" }, "#" + t.number),
      el("div", { class: "t-emphasis" }, t.title),
      el("div", { class: "krow" },
        el("span", { class: statusClass(t.status), "data-attention-key":t.status === "awaiting-human" ? `ticket:${pid}:${t.number}` : null }, t.status),
        el("span", { class: "t-chrome prio-" + (t.priority || "medium") }, chromeValue(t.priority)),
        assigneeChip(t.assignee)),
      el("div", { class: "t-chrome faint kage" }, `in ${t.status} · ${timeAgo(t.updated_at)}`));
    card.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/ticket-number", String(t.number));
      card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    return Motion.once(card, `ticket:${pid}:${t.number}`);
  }

  const refresh = debounce(() => { if (location.hash.includes("/board")) Views.board(); }, 400);
  AC.on("ticket_created", refresh, "wk-created");
  AC.on("ticket_updated", refresh, "wk-updated");
  AC.on("board_updated", refresh, "wk-board");
};

/* ---------- ticket list ---------- */
Views.tickets = async function () {
  const pid = AC.state.pid;
  const state = { status: "", assignee: "", priority: "", q: "" };
  const listBox = el("div", { class: "card", style: "padding:0" });

  const load = async () => {
    listBox.replaceChildren(el("div", { class: "spinner" }, "loading…"));
    try {
      const data = await API.tickets(pid, {
        status: state.status || undefined, assignee: state.assignee || undefined,
        priority: state.priority || undefined, q: state.q || undefined, limit: 200,
      });
      const items = data.items || [];
      listBox.replaceChildren(items.length
        ? el("div", {}, items.map((t) => ticketRow(t)))
        : el("div", { class: "empty" },
            el("div", { class: "glyph" }, "⌀"),
            el("div", { class: "e-title" }, "No tickets match"),
            el("p", {}, "Adjust the filters, or create a ticket."),
            el("button", {class:"btn", onclick:() => Views.ticketNew()}, "Create Ticket")));
    } catch (e) { listBox.replaceChildren(el("div", { class: "empty" }, el("p", {}, e.message))); }
  };

  function ticketRow(t) {
    const row = Motion.once(el("div", { "data-ticket":t.number, class: "trow" + (t.status === "awaiting-human" ? " awaiting" : ""), onclick: () => location.hash = `#/p/${pid}/tickets/${t.number}` },
      el("span", { class: "tnum" }, "#" + t.number),
      el("span", { class: "ttitle" }, t.title),
      ...(t.labels || []).slice(0, 3).map((l) => el("span", { class: "label-chip" }, l)),
      el("span", { class: "t-chrome prio-" + (t.priority || "medium") }, chromeValue(t.priority)),
      el("span", { class: statusClass(t.status), "data-attention-key":t.status === "awaiting-human" ? `ticket:${pid}:${t.number}` : null }, t.status),
      t.assignee ? assigneeChip(t.assignee) : el("span", { class: "t-chrome faint" }, "Unassigned")), `ticket:${pid}:${t.number}`);
    return row;
  }

  const statusSel = el("select", {}, el("option", { value: "" }, "Any status"),
    AC.state.statuses.map((s) => el("option", { value: s }, chromeValue(s))));
  const prioSel = el("select", {}, el("option", { value: "" }, "Any priority"),
    ["low", "medium", "high", "urgent"].map((p) => el("option", { value: p }, chromeValue(p))));
  const assigneeSel = el("select", {}, el("option", { value: "" }, "Any assignee"),
    el("option", { value: AC.state.adminAlias }, AC.state.adminAlias + " (admin)"),
    AC.state.agents.map((a) => el("option", { value: a.alias }, a.alias)));
  const qInput = el("input", { type: "text", placeholder: "filter text…" });
  statusSel.onchange = () => { state.status = statusSel.value; load(); };
  prioSel.onchange = () => { state.priority = prioSel.value; load(); };
  assigneeSel.onchange = () => { state.assignee = assigneeSel.value; load(); };
  qInput.oninput = debounce(() => { state.q = qInput.value.trim(); load(); }, 350);

  AC.setView(el("div", { class: "page narrow" },
    el("div", { class: "spread" },
      el("h1", {}, "Tickets"),
      el("button", { class: "btn primary", onclick: () => Views.ticketNew() }, "Create Ticket")),
    el("div", { class: "filter-bar" }, statusSel, prioSel, assigneeSel, qInput),
    listBox));
  await load();
  const refresh = debounce(load, 400);
  AC.on("ticket_created", refresh, "wk-created");
  AC.on("ticket_updated", refresh, "wk-updated");
};

/* ---------- new ticket ---------- */
Views.ticketNew = async function () {
  const pid = AC.state.pid;
  const title = el("input", { type: "text", maxlength: 200, placeholder: "Short summary" });
  const desc = el("textarea", { rows: 8, placeholder: "Markdown description…" });
  const prio = el("select", {}, ["low", "medium", "high", "urgent"].map((p) =>
    el("option", { value: p, selected: p === "medium" }, chromeValue(p))));
  const labels = el("input", { type: "text", placeholder: "labels, comma-separated" });
  const assignee = el("select", {}, el("option", { value: "" }, "Unassigned"),
    el("option", { value: AC.state.adminAlias }, AC.state.adminAlias + " (admin)"),
    AC.state.agents.filter((a) => !a.revoked).map((a) => el("option", { value: a.alias }, a.alias)));
  await modal({
    title: "Create Ticket", icon: "ticket", confirmText: "Create Ticket",
    isDirty: () => !!(title.value || desc.value || labels.value || assignee.value || prio.value !== "medium"),
    body: el("div", {},
      el("div", {class:"field"}, el("label", {}, "Title"), title),
      el("div", {class:"field"}, el("label", {}, "Description (markdown)"), desc),
      el("div", {class:"field"}, el("label", {}, "Priority"), prio),
      el("div", {class:"field"}, el("label", {}, "Assignee"), assignee),
      el("div", {class:"field"}, el("label", {}, "Labels"), labels)),
    onValidate: () => !!title.value.trim(),
    onConfirm: async () => {
      const ticket = await API.createTicket(pid, {
        title:title.value.trim(), description:desc.value, priority:prio.value,
        assignee:assignee.value || undefined,
        labels:labels.value.split(",").map(s => s.trim()).filter(Boolean),
      });
      toast("Ticket Created", "#" + ticket.number + " " + ticket.title);
      location.hash = `#/p/${pid}/tickets/${ticket.number}`;
    },
  });
};

/* ---------- ticket detail ---------- */
Views.ticketDetail = async function (n) {
  const collaborationRoute = location.hash;
  const pid = AC.state.pid;
  let t;
  try { t = await API.ticket(pid, n); }
  catch (e) { toast("Not Found", e.message, "warn"); location.hash = `#/p/${pid}/tickets`; return; }

  const statusSel = el("select", {}, AC.state.statuses.map((s) =>
    el("option", { value: s, selected: s === t.status }, chromeValue(s))));
  statusSel.onchange = async () => {
    try { await API.updateTicket(pid, n, { status: statusSel.value, expected_revision: t.revision }); toast("Status", `#${n} → ${statusSel.value}`); }
    catch (e) { toast("Error", e.message, "error"); Views.ticketDetail(n); }
  };
  const assigneeSel = el("select", {}, el("option", { value: "" }, "Unassigned"),
    el("option", { value: AC.state.adminAlias, selected: t.assignee === AC.state.adminAlias }, AC.state.adminAlias + " (admin)"),
    AC.state.agents.map((a) => el("option", { value: a.alias, selected: t.assignee === a.alias }, a.alias)));
  assigneeSel.onchange = async () => {
    try { await API.updateTicket(pid, n, { assignee: assigneeSel.value || null, expected_revision: t.revision }); }
    catch (e) { toast("Error", e.message, "error"); Views.ticketDetail(n); }
  };

  const commentsBox = el("div", {});
  const backlinksBox = el("div", {});

  const page = el("div", { class: "page narrow" },
    el("div", { class: "row", style: "gap:12px;margin-bottom:16px" },
      el("a", { href: `#/p/${pid}/tickets`, class: "muted" }, "← Tickets"),
      el("span", { class: "t-display", style: "margin:0" }, `#${t.number} ${t.title}`),
      t.status === "awaiting-human" ? el("span", { class: "stat-pill awaiting" }, "Needs your input") : null),
    el("div", { class: "card" },
      el("div", { class: "ticket-meta" },
        el("span", { class: "k" }, "Status"), el("div", {}, statusSel),
        el("span", { class: "k" }, "Assignee"), el("div", {}, assigneeSel),
        el("span", { class: "k" }, "Priority"), el("div", {}, el("span", { class: "t-chrome prio-" + (t.priority || "medium") }, chromeValue(t.priority))),
        el("span", { class: "k" }, "Labels"), el("div", {}, (t.labels || []).length ? (t.labels || []).map((l) => el("span", { class: "label-chip", style: "margin-right:4px" }, l)) : el("span", { class: "faint" }, "none")),
        el("span", { class: "k" }, "Reporter"), el("div", { class: "row" }, AC.avatarEl(t.reporter, 16), t.reporter),
        el("span", { class: "k" }, "Created"), el("div", { class: "muted small" }, fmtTime(t.created_at) + " · updated " + timeAgo(t.updated_at))),
      t.description ? renderMd(t.description) : el("p", { class: "faint" }, "No description."),
      t.status !== "done" && !t.assignee ? el("p", { class: "hint" }, "Unclaimed — agents claim via POST …/tickets/" + n + "/claim") : null),
    el("div", { class: "card" },
      el("div", { class: "rail-title" }, "Comments"),
      commentsBox,
      buildTicketCommentComposer(pid, n, () => loadComments())),
    el("div", { class: "card" },
      el("div", { class: "rail-title" }, "Mentioned in"),
      backlinksBox));
  if (AC.state.adminAlias) page.append(el("div", {class:"card"},
    el("h2", {}, "Delete Ticket"),
    el("p", {class:"muted"}, "Permanently delete this ticket and its comments."),
    el("button", {class:"btn danger", onclick:async () => {
      if (!await modal({title:"Delete Ticket #" + n + "?", danger:true, confirmText:"Delete Ticket",
        body:el("p", {}, "This permanently deletes " + t.title + " and its comments. This cannot be undone.")})) return;
      try { await API.deleteTicket(pid, n); toast("Ticket Deleted", "#" + n); location.hash = `#/p/${pid}/tickets`; }
      catch (e) { toast("Delete Ticket Failed", e.message, "error"); }
    }}, "Delete Ticket")));
  page.append(await Collaboration.ticketControls(pid, t));
  page.append(await Collaboration.evidenceControls(pid, t));
  if (location.hash !== collaborationRoute) return;
  AC.setView(page);

  async function loadComments() {
    try {
      const data = await API.ticketComments(pid, n);
      const items = data.items || [];
      commentsBox.replaceChildren(items.length
        ? el("div", {}, items.map((c) => {
            // The API serializes the author kind as `role` (author_type kept
            // as a fallback for safety) — the admin pill must always render.
            const role = c.role || c.author_type;
            return el("div", { class: "comment" + (role === "system" ? " system" : "") },
              el("div", { class: "c-row" },
                AC.avatarEl(c.author || c.author_alias, 20),
                el("span", { class: "m-author" }, c.author || c.author_alias),
                role === "admin" ? el("span", { class: "role-tag admin" }, "Admin") : null,
                el("span", { class: "m-time" }, fmtTime(c.created_at))),
              renderMd(c.body));
          }))
        : el("div", { class: "faint small" }, "No comments yet."));
    } catch (e) { commentsBox.replaceChildren(el("div", { class: "faint" }, e.message)); }
  }
  await loadComments();

  (() => {
    const links = t.backlinks || t.mentioned_in || [];
    backlinksBox.replaceChildren(links.length
      ? el("div", {}, links.map((b) => el("div", { class: "activity-line" },
          b.source_type === "message" && b.conversation_id
            ? el("a", { href: `#/p/${pid}/c/${b.conversation_id}` }, `message from ${b.author || "?"} · ${fmtTime(b.created_at)}`)
            : el("span", { class: "muted" }, `${b.source_type === "ticket_comment" ? "ticket comment" : (b.source_type || "ref")} by ${b.author || "?"} · ${fmtTime(b.created_at)}`),
          b.excerpt ? el("span", { class: "small muted" }, b.excerpt.slice(0, 160)) : null)))
      : el("div", { class: "faint small" }, "Not referenced anywhere yet."));
  })();

  const refresh = debounce(() => { if (location.hash.endsWith(`/tickets/${n}`)) Views.ticketDetail(n); }, 500);
  AC.on("ticket_updated", (pl) => { if ((pl.ticket && pl.ticket.number) === n) refresh(); }, "tdetail");
  AC.on("ticket_comment", (pl) => { if (pl.ticket_number === n) loadComments(); }, "tdetail-c");
  AC.on("evidence_updated", (pl) => { if (pl.ticket_number === n) refresh(); }, "tdetail-e");
  AC.on("policy_updated", (pl) => { if (pl.ticket_number === n) refresh(); }, "tdetail-pol");
  AC.on("completion_requested", (pl) => { if (pl.ticket_number === n) refresh(); }, "tdetail-p");
  AC.on("completion_reviewed", (pl) => { if (pl.ticket_number === n) refresh(); }, "tdetail-r");
};

function buildTicketCommentComposer(pid, n, onPosted) {
  const ta = el("textarea", { rows: 3, placeholder: "Comment (markdown)…", style: "width:100%" });
  const send = async () => {
    const body = ta.value.trim();
    if (!body) return;
    try { await API.postTicketComment(pid, n, { body }); ta.value = ""; onPosted(); }
    catch (e) { toast("Comment Failed", e.message, "error"); }
  };
  ta.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  return el("div", { class: "composer", style: "margin-top:12px" }, ta,
    el("div", { class: "send-row" },
      el("span", { class: "composer-hint" }, "enter to send · shift+enter newline · markdown"),
      el("span", { class: "spacer" }),
      el("button", { class: "btn-send", onclick: send }, "Comment")));
}

