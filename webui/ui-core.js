/* Core shell: state, hash router, SSE stream, sidebar, statusbar live wire,
   toasts, modals, topbar, bell, avatar helper. */
"use strict";

const AC = {
  state: {
    setupComplete: null,
    adminAlias: null,
    adminColor: "#e0b040",
    adminAvatar: "",
    projects: [],
    // Current project context (loaded when entering #/p/{pid}/...)
    pid: null,
    project: null,
    channels: [],
    dms: [],
    agents: [],
    statuses: [],
    unseenMentions: [],
    emojiMap: {},
    // localStorage-backed "last message id I saw per conversation" (the admin
    // has no server-side read cursor; agents do — contract limitation).
    lastSeen: {},
    collapsed: {}, // sidebar sections
  },

  // ---------- boot ----------
  async boot() {
    try { AC.state.lastSeen = JSON.parse(localStorage.getItem("ac_last_seen") || "{}"); } catch (e) {}
    try { AC.state.collapsed = JSON.parse(localStorage.getItem("ac_collapsed") || "{}"); } catch (e) {}
    try {
      const st = await API.setupStatus();
      AC.state.setupComplete = st.setup_complete;
      if (st.admin) {
        AC.state.adminAlias = st.admin.alias;
        AC.state.adminColor = st.admin.color || "#e0b040";
        AC.state.adminAvatar = st.admin.avatar || "";
      }
    } catch (e) {
      document.getElementById("view").replaceChildren(
        el("div", { class: "page narrow" },
          el("div", { class: "card" }, "Cannot reach the Nomos server: " + e.message)));
      return;
    }
    // Emoji vocabulary (status emoji in the sidebar, reactions later). Not in
    // api.js yet — fetched directly; failure is cosmetic only.
    try {
      const p = await (await fetch("/api/emoji")).json();
      if (p && p.ok) AC.state.emojiMap = p.data.emoji || {};
    } catch (e) { /* cosmetic only */ }
    window.addEventListener("hashchange", () => AC.route());
    const statusbar = document.getElementById("statusbar");
    statusbar.setAttribute("role", "button"); statusbar.tabIndex = 0;
    statusbar.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); statusbar.click(); } });
    document.getElementById("statusbar").addEventListener("click", () => {
      if (AC.state.pid) location.hash = `#/p/${AC.state.pid}/activity`;
    });
    const themeBtn = document.getElementById("theme-toggle");
    const syncThemeBtn = () => {
      const dark = document.documentElement.dataset.theme !== "light";
      themeBtn.title = dark ? "Switch to Light Theme" : "Switch to Dark Theme";
      themeBtn.setAttribute("aria-label", themeBtn.title);
      themeBtn.setAttribute("aria-pressed", String(!dark));
    };
    themeBtn.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("ac-theme", next); } catch (e) {}
      syncThemeBtn();
    });
    themeBtn.replaceChildren(Icons.theme());
    document.getElementById("bell").prepend(Icons.bell());
    document.getElementById("bell").childNodes.forEach(n => { if (n.nodeType === 3) n.remove(); });
    syncThemeBtn();
    AC.renderTopbar();
    AC.route();
  },

  saveLastSeen() {
    try { localStorage.setItem("ac_last_seen", JSON.stringify(AC.state.lastSeen)); } catch (e) {}
  },

  emoji(shortcode) {
    return AC.state.emojiMap[shortcode] || "";
  },

  // ---------- avatar helper ----------
  // AC.avatarEl(aliasOrObj, size): SVG dial avatar when a slug is known,
  // hue-hashed initials otherwise. Sizes map to fixed radii via CSS classes.
  avatarEl(who, size) {
    const cls = "avatar s" + (size || 36);
    let alias = "", slug = "";
    if (typeof who === "string") {
      alias = who;
      slug = AC.lookupAvatar(who);
    } else if (who) {
      alias = who.alias || who.author || "?";
      slug = who.avatar !== undefined ? who.avatar : AC.lookupAvatar(alias);
    }
    if (slug) {
      return el("span", { class: cls }, el("img", { src: `/avatars/${slug}.svg`, alt: alias }));
    }
    const node = el("span", { class: cls }, initials(alias));
    node.style.background = avatarColor(alias);
    return node;
  },

  lookupAvatar(alias) {
    if (!alias) return "";
    if (AC.state.adminAlias && alias.toLowerCase() === AC.state.adminAlias.toLowerCase()) {
      return AC.state.adminAvatar;
    }
    const agent = AC.state.agents.find((a) => a.alias.toLowerCase() === alias.toLowerCase());
    return (agent && agent.avatar) || "";
  },

  agentByAlias(alias) {
    return AC.state.agents.find((a) => a.alias.toLowerCase() === String(alias || "").toLowerCase()) || null;
  },

  // ---------- router ----------
  _routeSeq: 0,
  async route() {
    const hash = location.hash || "#/";
    const token = ++AC._routeSeq;
    if (activeModal && activeModal.beforeRoute && activeModal.beforeRoute(hash)) return;
    if (AC._leaveGuard && hash !== AC._viewHash) {
      const allowed = await AC._leaveGuard();
      if (token !== AC._routeSeq) return; // superseded while prompting
      if (!allowed) { history.replaceState(null, "", AC._viewHash); return; }
      AC._leaveGuard = null;
    }
    if (activeModal) closeModal(false);
    const parts = hash.replace(/^#\//, "").split("/").filter(Boolean);

    if (!AC.state.setupComplete) { Views.setup(); return; }
    AC.busReset();
    AC.closeThreadPanel();
    // Overlay roots must not survive navigation (their own outside-close
    // listeners are one-shot and self-remove on the next pointer event).
    document.getElementById("lightbox-root").replaceChildren();
    document.getElementById("popover-root").replaceChildren();

    try {
      if (parts.length === 0) { await AC.leaveProject(); await Views.dashboard(); }
      else if (parts[0] === "p" && parts[1]) {
        const pid = Number(parts[1]);
        await AC.enterProject(pid);
        // Sidebar "+" handlers live for the whole route, on EVERY project
        // view — per-view registration left them dead on most (issue #29).
        registerSidebarAdders();
        const sub = parts[2] || "chat";
        if (sub === "chat" || sub === "c") await Views.projectChat(parts[3] ? Number(parts[3]) : null, parts[5] ? Number(parts[5]) : null);
        else if (sub === "board") await Views.board();
        else if (sub === "tickets" && parts[3] === "new") { await Views.tickets(); Views.ticketNew(); }
        else if (sub === "tickets" && parts[3]) await Views.ticketDetail(Number(parts[3]));
        else if (sub === "tickets") await Views.tickets();
        else if (sub === "docs" && parts[3] && parts[4] === "edit") await Views.docEdit(decodeURIComponent(parts[3]));
        else if (sub === "docs" && parts[3]) await Views.docView(decodeURIComponent(parts[3]));
        else if (sub === "docs") await Views.docs();
        else if (sub === "search") await Views.search();
        else if (sub === "agents") await Views.agentsAdmin();
        else if (sub === "settings") await Views.settings();
        else if (sub === "dashboard") await Views.projectDashboard();
        else if (sub === "decisions") await Views.decisions();
        else if (Object.prototype.hasOwnProperty.call(Views, sub)) await Views[sub]();
        else await Views.projectChat(null, null);
      } else { location.hash = "#/"; return; }
    } catch (e) {
      toast("Error", e.message, "error");
      AC.setView(el("div", { class: "page narrow" }, el("div", { class: "card" }, "Failed to load view: " + e.message)));
    }
    AC.renderTopbar();
    AC.renderSidebar();
  },

  setView(node) {
    const changed = AC._viewHash !== location.hash;
    AC._viewHash = location.hash;
    if (changed && !Motion.reduced()) node.classList.add("view-enter");
    document.getElementById("view").replaceChildren(node);
  },

  closeThreadPanel() {
    const panel = document.getElementById("thread-panel");
    panel.classList.add("hidden");
    panel.replaceChildren();
  },

  // ---------- project context ----------
  async enterProject(pid) {
    if (AC.state.pid === pid && AC.state.project) { await AC.refreshSidebarData(); return; }
    await AC.leaveProject();
    AC.state.pid = pid;
    const [project, channels, dms, agents, statuses] = await Promise.all([
      API.project(pid), API.channels(pid), API.dms(pid), API.agents(pid), API.projectStatuses(pid),
    ]);
    AC.state.project = project;
    AC.state.channels = channels.items || channels;
    AC.state.dms = dms.items || dms;
    AC.state.agents = agents.items || agents;
    AC.state.statuses = statuses.statuses || [];
    document.getElementById("app").classList.remove("no-sidebar");
    AC.connectStream(pid);
    AC.refreshMentions();
  },

  async refreshSidebarData() {
    const pid = AC.state.pid;
    const [channels, dms, agents] = await Promise.all([API.channels(pid), API.dms(pid), API.agents(pid)]);
    AC.state.channels = channels.items || channels;
    AC.state.dms = dms.items || dms;
    AC.state.agents = agents.items || agents;
    AC.renderSidebar();
  },

  async leaveProject() {
    AC.closeStream();
    AC.state.pid = null;
    AC.state.project = null;
    AC.state.unseenMentions = [];
    document.getElementById("app").classList.add("no-sidebar");
    document.getElementById("sidebar").replaceChildren();
    AC.setWireIdle();
    AC.updateBell();
  },

  // ---------- sidebar ----------
  renderSidebar() {
    const root = document.getElementById("sidebar");
    if (!AC.state.pid || !AC.state.project) { root.replaceChildren(); return; }
    const pid = AC.state.pid;
    const parts = location.hash.replace(/^#\//, "").split("/");
    const sub = parts[2] || "chat";
    const activeConv = (sub === "c" && parts[3]) ? Number(parts[3]) : null;

    const mentionsByConv = {};
    for (const m of AC.state.unseenMentions) {
      const cid = m.conversation_id || (m.message && m.message.conversation_id);
      if (cid) mentionsByConv[cid] = (mentionsByConv[cid] || 0) + 1;
    }
    const unreadOf = (conv) =>
      conv.last_message_id && conv.last_message_id > (AC.state.lastSeen[conv.id] || 0);

    const navRow = (key, glyph, label, badge) =>
      el("a", {
        class: "side-row" + (sub === key ? " active" : ""),
        href: `#/p/${pid}/${key}`,
      },
        el("span", { class: "glyph" }, (Icons[key] || Icons.decisions)()),
        el("span", { class: "name" }, label),
        badge ? el("span", { class: "count" }, String(badge)) : null);

    const channelRow = (c) => {
      const mentions = mentionsByConv[c.id] || 0;
      return el("a", {
        class: "side-row" + (activeConv === c.id ? " active" : "")
          + (unreadOf(c) ? " unread" : "") + (mentions ? " has-mentions" : ""),
        href: `#/p/${pid}/c/${c.id}`,
      },
        el("span", { class: "glyph" }, "#"),
        el("span", { class: "name" }, c.name),
        mentions ? el("span", { class: "count" }, String(mentions)) : null);
    };

    const dmRow = (d) => {
      const label = d.with || (d.participants || []).join(" ↔ ") || "conversation";
      const other = d.with ? AC.agentByAlias(d.with) : null;
      const mentions = mentionsByConv[d.id] || 0;
      const av = el("span", { class: "av-wrap" },
        AC.avatarEl(other || label, 20),
        other ? el("span", { class: "presence " + (other.online ? "online" : "away"), title:other.online ? "Online" : "Away", "aria-label":other.online ? "Online" : "Away" }, other.online ? Icons.check() : Icons.minus()) : null);
      return el("a", {
        class: "side-row" + (activeConv === d.id ? " active" : "")
          + (unreadOf(d) ? " unread" : "") + (mentions ? " has-mentions" : ""),
        href: `#/p/${pid}/c/${d.id}`,
      },
        av,
        el("span", { class: "name" }, label),
        other && other.status_emoji
          ? el("span", { class: "status-emoji", title: other.status_text || "" }, AC.emoji(other.status_emoji))
          : null,
        mentions ? el("span", { class: "count" }, String(mentions)) : null);
    };

    const section = (key, label, rows, onAdd) => {
      const collapsed = !!AC.state.collapsed[key];
      const group = el("div", { class: "side-group" + (collapsed ? " collapsed" : "") },
        el("div", {
          class: "side-section",
          onclick: (e) => {
            if (e.target.closest(".add")) return;
            AC.state.collapsed[key] = !collapsed;
            try { localStorage.setItem("ac_collapsed", JSON.stringify(AC.state.collapsed)); } catch (err) {}
            AC.renderSidebar();
          },
        },
          el("span", { class: "caret" }, collapsed ? "▸" : "▾"),
          el("span", {class:"section-icon"}, key === "channels" ? Icons.channels() : Icons.chat()),
          el("span", {}, label),
          el("span", { class: "flex" }),
          onAdd ? el("button", { class: "add", title: "Add", onclick: onAdd }, "+") : null),
        ...rows);
      return group;
    };

    const totalMentions = AC.state.unseenMentions.length;
    root.replaceChildren(
      el("a", { class: "side-project", href: `#/p/${pid}/settings` },
        el("span", { class: "name" }, AC.state.project.name),
        el("span", { class: "chev" }, "▾")),
      el("div", { class: "side-nav" },
        navRow("activity", "≡", "Activity"),
        navRow("mentions", "@", "Mentions", totalMentions || null),
        navRow("saved", "⚑", "Saved"),
        navRow("decisions", "◆", "Decisions"),
        navRow("pins", "⌖", "Pins"),
        navRow("inbox", "↪", "Reply Requests"),
        navRow("context", "↻", "Resume Context"),
        navRow("decisionQueue", "?", "Decision Queue"),
        navRow("attention", "!", "Attention"),
        navRow("workspaces", "⌂", "Workspaces"),
        navRow("outcomes", "↗", "Outcomes")),
      section("channels", "Channels", AC.state.channels.map(channelRow),
        () => AC._dispatch("sidebar_add_channel", null)),
      section("dms", "Direct Messages", AC.state.dms.map(dmRow),
        () => AC._dispatch("sidebar_add_dm", null)),
    );
  },

  // ---------- SSE ----------
  _es: null,
  _lastEventId: 0,
  _reconnectTimer: null,

  connectStream(pid) {
    AC.closeStream();
    const open = (sinceId) => {
      const es = new EventSource(API.streamUrl(pid, sinceId));
      AC._es = es;
      es.onopen = () => AC.setWireIdle();
      es.onerror = () => {
        // Never trust the browser's auto-reconnect (it replays via
        // Last-Event-ID only): always close and reopen ourselves with an
        // explicit since_id so the server-side replay guarantee applies.
        es.close();
        AC.setWireOffline();
        if (AC.state.pid === pid && AC._es === es) {
          AC._reconnectTimer = setTimeout(() => open(AC._lastEventId), 2000);
        }
      };
      const types = ["message", "mention", "message_edited", "message_deleted", "message_pinned",
        "message_unpinned", "reaction", "typing", "ticket_created", "ticket_updated",
        "ticket_comment", "awaiting_human", "document_created", "document_updated",
        "agent_joined", "agent_updated", "agent_revoked", "agent_removed",
        "channel_created", "dm_opened", "board_updated", "audit", "audit_anomaly", "inbox_requested", "inbox_updated", "checkpoint_created", "handoff_updated", "decision_requested", "decision_resolved", "evidence_updated", "policy_updated", "completion_requested", "completion_reviewed", "workspace_updated", "reservation_updated", "reservation_conflict", "experiment_updated"];
      const TRANSIENT = new Set(["typing"]);
      for (const t of types) {
        es.addEventListener(t, (ev) => {
          // Careful: per the SSE spec, ev.lastEventId PERSISTS from earlier
          // events, so an id-less transient event still reports the previous
          // durable id. Dedupe applies only to durable (replayable) types.
          if (!TRANSIENT.has(t)) {
            const evId = Number(ev.lastEventId) || 0;
            if (evId && evId <= AC._lastEventId) return; // duplicate after reconnect
            if (evId) AC._lastEventId = evId;
          }
          let payload = {};
          try { payload = JSON.parse(ev.data); } catch (e) {}
          AC.handleEvent(t, payload);
        });
      }
    };
    open(undefined);
  },

  closeStream() {
    if (AC._reconnectTimer) { clearTimeout(AC._reconnectTimer); AC._reconnectTimer = null; }
    if (AC._es) { AC._es.close(); AC._es = null; }
    AC._lastEventId = 0;
  },

  // ---------- statusbar live wire (design signature) ----------
  _wireTimer: null,

  setWireIdle() {
    const dot = document.getElementById("wire-dot");
    dot.classList.remove("off", "blip");
    const meta = document.getElementById("wire-meta");
    meta.textContent = AC.state.project ? AC.state.project.name : "";
  },

  setWireOffline() {
    const dot = document.getElementById("wire-dot");
    dot.classList.remove("blip");
    dot.classList.add("off");
    document.getElementById("wire-meta").textContent = "reconnecting…";
  },

  wire(type, payload) {
    if (type === "typing") return; // too chatty for the tape
    const dot = document.getElementById("wire-dot");
    dot.classList.remove("off");
    dot.classList.add("blip");
    if (AC._wireTimer) clearTimeout(AC._wireTimer);
    AC._wireTimer = setTimeout(() => dot.classList.remove("blip"), 300);

    const tape = document.getElementById("wire-tape");
    const now = new Date();
    const hh = String(now.getHours()).padStart(2, "0");
    const mm = String(now.getMinutes()).padStart(2, "0");
    const ss = String(now.getSeconds()).padStart(2, "0");
    const item = el("span", { class: "tape-item" },
      el("span", { class: "tape-time" }, `${hh}:${mm}:${ss}`),
      AC.tapeLabel(type, payload));
    tape.prepend(item);
    while (tape.children.length > 5) tape.lastChild.remove();
  },

  tapeLabel(type, pl) {
    pl = pl || {};
    const t = pl.ticket || {};
    const tno = pl.ticket_number ?? t.number;
    const conv = (cid) => {
      const c = AC.state.channels.find((x) => x.id === cid);
      return c ? "#" + c.name : "dm";
    };
    switch (type) {
      case "message": return `${pl.author || "?"} → ${conv(pl.conversation_id)}`;
      case "mention": return `${pl.by || "?"} mentioned you`;
      case "reaction": return `${pl.actor || "?"} ${pl.reacted ? "+" : "−"}:${pl.emoji}:`;
      case "ticket_created": return `ticket #${tno} opened`;
      case "ticket_updated": return `ticket #${tno} → ${t.status || "updated"}`;
      case "ticket_comment": return `comment on #${pl.ticket_number}`;
      case "awaiting_human": return `#${tno} awaits you`;
      case "document_created": return `doc ${pl.slug} created`;
      case "document_updated": return `doc ${pl.slug} rev ${pl.revision || ""}`;
      case "agent_joined": return `${pl.alias || "agent"} joined`;
      case "agent_updated": return `${(pl.agent && pl.agent.alias) || "agent"} updated profile`;
      case "agent_revoked": return `${pl.alias || "agent"} revoked`;
      case "agent_removed": return `${pl.alias || "agent"} removed`;
      case "message_pinned": return "message pinned";
      case "dm_opened": return "dm opened";
      case "channel_created": return `#${pl.name || "channel"} created`;
      default: return type.replace(/_/g, " ");
    }
  },

  // ---------- event dispatch ----------
  // Listeners are tagged with the route generation they were registered in;
  // dispatch ignores stale generations, so a slow view that finishes loading
  // after the user navigated away cannot leave live handlers behind.
  _bus: {},
  _busGen: 0,
  busReset() { AC._bus = {}; AC._busGen++; },
  // `key` makes registration idempotent: a view that re-renders itself on an
  // event re-registers every render, and without replacement the bucket
  // doubles per event (2^k handlers — issue #28 H12).
  on(type, fn, key) {
    const bucket = (AC._bus[type] = AC._bus[type] || []);
    if (key !== undefined) {
      const i = bucket.findIndex((h) => h.key === key);
      if (i !== -1) bucket.splice(i, 1);
    }
    bucket.push({ fn, gen: AC._busGen, key });
  },

  handleEvent(type, payload) {
    AC.wire(type, payload);
    // Global behaviors first
    if (type === "mention") {
      AC.refreshMentions();
      toast("Mention", `${payload.by || "someone"}: ${payload.excerpt || ""}`, "warn");
    } else if (type === "awaiting_human") {
      toast("Awaiting Human", `Ticket #${payload.ticket_number || "?"} needs your input`, "warn");
    } else if (type === "agent_joined") {
      toast("Agent Joined", `${payload.alias || "an agent"} joined the project`);
    } else if (type === "audit_anomaly") {
      toast("Unattributed Change", payload.path || payload.summary || "a watched file changed", "warn");
    }
    if (["agent_joined", "agent_updated", "agent_revoked", "agent_removed",
         "channel_created", "dm_opened"].includes(type)) {
      AC.refreshSidebarData().then(() => AC._dispatch("sidebar_refresh", null)).catch(() => {});
    } else if (type === "message") {
      AC.renderSidebar(); // unread bolding
    }
    AC._dispatch(type, payload);
  },

  _dispatch(type, payload) {
    for (const entry of AC._bus[type] || []) {
      if (entry.gen !== AC._busGen) continue; // stale route's listener
      try {
        const r = entry.fn(payload);
        if (r && typeof r.catch === "function") {
          r.catch((e) => console.error("event handler failed", type, e));
        }
      } catch (e) { console.error("event handler failed", type, e); }
    }
  },

  // ---------- mentions bell ----------
  async refreshMentions() {
    if (!AC.state.pid) return;
    try {
      const data = await API.mentions(AC.state.pid, true);
      AC.state.unseenMentions = data.items || data || [];
    } catch (e) { AC.state.unseenMentions = []; }
    AC.updateBell();
    AC.renderSidebar();
  },

  updateBell() {
    const wrap = document.getElementById("bell-wrap");
    const badge = document.getElementById("bell-badge");
    if (!AC.state.pid) { wrap.classList.add("hidden"); return; }
    wrap.classList.remove("hidden");
    const n = AC.state.unseenMentions.length;
    badge.textContent = n;
    badge.classList.toggle("hidden", n === 0);
  },

  // ---------- topbar ----------
  renderTopbar() {
    const chip = document.getElementById("admin-chip");
    if (AC.state.adminAlias) {
      chip.textContent = AC.state.adminAlias + " · admin";
      chip.classList.remove("hidden");
    }
    const nav = document.getElementById("project-nav");
    if (!AC.state.pid || !AC.state.project) { nav.replaceChildren(); AC.updateBell(); return; }
    const pid = AC.state.pid;
    const cur = (location.hash.replace(/^#\//, "").split("/")[2] || "chat");
    const tabs = [
      ["chat", "Chat"], ["board", "Board"], ["tickets", "Tickets"], ["docs", "Docs"],
      ["search", "Search"], ["agents", "Agents"], ["audit", "Audit"], ...(AC.state.adminAlias ? [["dashboard", "Dashboard"]] : []), ["settings", "Settings"],
    ];
    nav.replaceChildren(
      el("span", { class: "proj-name" }, AC.state.project.name,
        AC.state.project.archived ? el("span", { class: "pill archived", style: "margin-left:8px" }, "archived") : null),
      ...tabs.map(([key, label]) =>
        el("a", { class: "tab" + ((cur === key || (key === "chat" && cur === "c")) ? " active" : ""), href: `#/p/${pid}/${key}` }, (Icons[key] || Icons.circle)(), label))
    );
    AC.updateBell();
  },
};

/* ---------- toasts ---------- */
function toast(title, msg, kind) {
  const t = el("div", { class: "toast" + (kind ? " " + kind : ""), role:kind === "error" ? "alert" : "status" },
    el("div", { class: "t-title" }, kind === "error" ? Icons.block() : kind === "warn" ? Icons.attention() : Icons.check(), title), el("div", {}, String(msg || "") + (kind === "error" ? " Review the details and try again." : "")));
  document.getElementById("toasts").append(t);
  setTimeout(() => { t.classList.add("closing"); setTimeout(() => t.remove(), Motion.reduced() ? 0 : 100); }, kind === "error" ? 8000 : 4500);
}

/* ---------- modals ---------- */
let activeModal = null;
let modalSequence = 0;
function closeModal(value = false) {
  if (activeModal) activeModal(value);
}
function modal({ title, body, confirmText = "Confirm", danger = false, onValidate, onConfirm, isDirty, icon }) {
  // Replacing a dialog settles the old promise before taking over its root.
  closeModal(false);
  return new Promise((resolve) => {
    const root = document.getElementById("modal-root");
    const opener = document.activeElement;
    const id = "modal-" + ++modalSequence;
    let closed = false, busy = false, discarding = false, pendingNavigation = null;
    const modalHash = location.hash || "#/";
    const app = document.getElementById("app");
    const priorInert = app.inert;
    app.inert = true;
    const error = el("p", {class:"modal-error danger-text", role:"alert", hidden:true});
    const content = el("div", {id:id + "-body"}, body);
    const heading = el("h3", {id:id + "-title"}, icon ? Icons[icon]() : Icons.forAction(title), title);
    const finish = (value, immediate = false) => {
      if (closed) return;
      closed = true;
      document.removeEventListener("keydown", keydown, true);
      document.removeEventListener("focusin", focusin, true);
      if (activeModal === cancelExternal) { activeModal = null; app.inert = priorInert; }
      const remove = () => {
        overlay.remove();
        if (!activeModal) app.inert = priorInert;
        resolve(value);
        // Focus returns AFTER the caller resumes: openers disabled during
        // their action re-enable in a finally, so give them one tick.
        if (!activeModal) setTimeout(() => {
          if (activeModal || !opener || !opener.isConnected) return;
          if (!opener.disabled) opener.focus();
        }, 0);
      };
      if (immediate || Motion.reduced()) remove();
      else { overlay.classList.add("closing"); setTimeout(remove, 100); }
    };
    const cancelExternal = (value) => finish(value, true);
    const cancel = () => {
      if (busy || closed) return;
      if (isDirty && isDirty() && !discarding) {
        discarding = true;
        heading.replaceChildren(Icons.trash(), "Discard Draft?");
        content.hidden = true;
        error.hidden = true;
        warning.hidden = false;
        confirmBtn.className = "btn danger";
        confirmBtn.replaceChildren(Icons.trash(), "Discard Draft");
        cancelBtn.replaceChildren(Icons.close(), "Keep Editing");
        cancelBtn.focus();
      } else if (discarding) {
        discarding = false; pendingNavigation = null;
        heading.replaceChildren(icon ? Icons[icon]() : Icons.forAction(title), title);
        content.hidden = false; warning.hidden = true;
        confirmBtn.className = "btn " + (danger ? "danger" : "primary");
        confirmBtn.replaceChildren(Icons.forAction(confirmText), confirmText);
        cancelBtn.replaceChildren(Icons.close(), "Cancel");
        focusFirst();
      } else finish(false);
    };
    const cancelBtn = el("button", {class:"btn quiet", onclick:cancel}, "Cancel");
    const confirmBtn = el("button", {
      class:"btn " + (danger ? "danger" : "primary"),
      onclick: async () => {
        if (busy || closed) return;
        if (discarding) { const target = pendingNavigation; finish(false); if (target) location.hash = target; return; }
        if (onValidate && !onValidate(box)) {
          error.textContent = "Check the required fields and try again.";
          error.hidden = false; return;
        }
        busy = true; confirmBtn.disabled = true; cancelBtn.disabled = true;
        try {
          if (onConfirm && await onConfirm() === false) return;
          finish(true);
        } catch (e) {
          error.textContent = confirmText + " failed: " + e.message + " Review the fields and try again.";
          error.hidden = false;
        } finally { busy = false; if (!closed) { confirmBtn.disabled = false; cancelBtn.disabled = false; } }
      },
    }, confirmText);
    const warning = el("p", {hidden:true}, "Your unsaved changes will be lost.");
    const box = el("div", {class:"modal", role:"dialog", "aria-modal":"true", "aria-labelledby":id+"-title", "aria-describedby":id+"-body", tabindex:"-1"},
      heading, content, warning, error, el("div", {class:"m-btns"}, confirmText === "Close" ? null : cancelBtn, confirmBtn));
    const overlay = el("div", {class:"modal-overlay"}, box);
    overlay.addEventListener("click", e => { if (e.target === overlay) cancel(); });
    const focusable = () => Array.from(box.querySelectorAll('button, input:not([type="hidden"]), select, textarea, a[href], [tabindex="0"]'))
      .filter(n => !n.disabled && !n.closest('[hidden]'));
    const focusFirst = () => (danger ? cancelBtn : focusable()[0] || box).focus();
    const focusin = e => { if (!box.contains(e.target) && !closed) focusFirst(); };
    const keydown = e => {
      if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); cancel(); }
      if (e.key === "Tab") {
        const nodes = focusable(), first = nodes[0] || box, last = nodes[nodes.length-1] || box;
        if (e.shiftKey && (document.activeElement === first || document.activeElement === box)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    cancelExternal.beforeRoute = target => {
      if (target === modalHash || busy || !isDirty || !isDirty()) return false;
      if (!discarding) cancel();
      pendingNavigation = target;
      history.replaceState(null, "", modalHash);
      return true;
    };
    root.append(overlay);
    activeModal = cancelExternal;
    document.addEventListener("keydown", keydown, true);
    document.addEventListener("focusin", focusin, true);
    focusFirst();
  });
}
async function reasonModal(title, message, danger = true) {
  const reason = el("textarea", {rows:3, required:true, placeholder:"Explain the reason"});
  const ok = await modal({title, danger, confirmText:title,
    body:el("div", {}, el("p", {}, message), el("label", {class:"field"}, "Reason", reason)),
    onValidate:() => !!reason.value.trim()});
  return ok ? reason.value.trim() : null;
}
async function confirmDiscard() {
  return modal({title:"Discard Draft?", body:el("p", {}, "Your unsaved changes will be lost."), confirmText:"Discard Draft", danger:true});
}

/* Bell dropdown behavior */
document.addEventListener("click", (e) => {
  const bell = document.getElementById("bell");
  const dd = document.getElementById("bell-dropdown");
  if (bell && bell.contains(e.target)) {
    dd.classList.toggle("hidden");
    if (!dd.classList.contains("hidden")) renderBellDropdown(dd);
  } else if (dd && !dd.contains(e.target)) {
    dd.classList.add("hidden");
  }
});

function renderBellDropdown(dd) {
  const items = AC.state.unseenMentions;
  dd.replaceChildren(
    el("div", { class: "dd-head" }, `Mentions (${items.length})`,
      items.length ? el("button", {
        class: "btn small",
        onclick: async (e) => {
          e.stopPropagation();
          try { await API.markMentionsSeen(AC.state.pid, true); } catch (err) { toast("Error", err.message, "error"); }
          await AC.refreshMentions();
          renderBellDropdown(dd);
        },
      }, "Mark All Read") : null),
    ...(items.length ? items.map((m) => el("div", {
      class: "dd-item",
      onclick: async () => {
        dd.classList.add("hidden");
        // Jump to the mention in context, and mark just this one seen.
        if (m.source === "ticket_comment" && m.comment) {
          location.hash = `#/p/${AC.state.pid}/tickets/${m.comment.ticket_number}`;
        } else if (m.message) {
          location.hash = `#/p/${AC.state.pid}/c/${m.message.conversation_id}/m/${m.message.id}`;
        }
        if (m.mention_id) {
          try { await API.markMentionsSeen(AC.state.pid, [m.mention_id]); } catch (e) {}
          AC.refreshMentions();
        }
      },
    },
      el("div", { class: "small muted" },
        `${((m.message && m.message.author) || (m.comment && m.comment.author) || "?")}` +
        ` · ${fmtTime(m.mentioned_at || m.created_at)}` +
        (m.comment ? ` · ticket #${m.comment.ticket_number}` : "")),
      el("div", {}, ((m.message && m.message.body) || (m.comment && m.comment.body) || "").slice(0, 140)),
    )) : [el("div", { class: "dd-empty" }, "No unseen mentions")])
  );
}

/* Views namespace: populated by ui-admin.js, ui-project.js, ui-work.js, ui-docs.js */
const Views = {};

