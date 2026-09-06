/* Nomos chrome: local, monochrome, 16px / 1.5px. Content emoji stay content. */
"use strict";
const Icons = (() => {
  // Each glyph uses at most four pen strokes (disconnected subpaths).
  // Already minimal utility/formatting marks retain their familiar geometry.
  const paths = {
    chat: 'M3 3h10v7H6l-3 3z',
    board: 'M3 3v10 M8 3v7 M13 3v10',
    ticket: 'M2 4h12v2a2 2 0 0 0 0 4v2H2v-2a2 2 0 0 0 0-4z',
    docs: 'M4 2h5l3 3v9H4z M7 8h2 M7 11h2',
    search: 'M11 11l3 3 M12 7a5 5 0 1 1-10 0 5 5 0 0 1 10 0',
    agents: 'M3 13a5 5 0 0 1 10 0',
    audit: 'M4 2h8v12H4z M6 6h4 M6 10h4',
    settings: 'M2 5h12 M2 11h12 M6 3v4 M10 9v4',
    activity: 'M2 8h3l2-4 3 8 2-4h2',
    mentions: 'M10.5 8a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0 M10.5 5.5v4c0 2.5 4 1.5 4-1.5a6.5 6.5 0 1 0-3 5.5',
    saved: 'M4 2h8v12l-4-3-4 3z',
    decisions: 'M8 2l6 6-6 6-6-6z',
    pins: 'M6 2h4v4l2 3H4l2-3z M8 9v5',
    inbox: 'M3 3h10v10H3z M3 8h3l1 2h2l1-2h3',
    context: 'M3 4a6 6 0 1 1-1 6 M2 1v4h4 M8 5v3l2 1',
    attention: 'M8 2l6 12H2z M8 6v3 M8 11h.01',
    workspaces: 'M2 4h5l2 2h5v7H2z',
    outcomes: 'M3 3v10h10 M6 10l3-4 2 2 3-4',
    channels: 'M6 2 4 14 M12 2l-2 12 M2 6h12 M2 10h12',
    attach: 'm5 9 5-5a2 2 0 0 1 3 3l-6 6a3.5 3.5 0 0 1-5-5l6-6',
    smile: 'M14 8A6 6 0 1 1 2 8a6 6 0 0 1 12 0 M5 6h.01 M11 6h.01 M5 9a3 3 0 0 0 6 0',
    send: 'M2 3l12 5-12 5 2-5z M4 8h10',
    thread: 'M6 3v6h8 M10 5l4 4-4 4',
    forward: 'M9 2l5 5-5 5 M14 7H7a5 5 0 0 0-5 5v2',
    edit: 'M3 10l8-8 3 3-8 8-4 1z',
    trash: 'M3 4h10 M6 2h4 M4 4l1 10h6l1-10',
    link: 'M6 10l4-4 M5 9l-2 2a2 2 0 0 0 3 3l3-3 M7 5l3-3a2 2 0 0 1 3 3l-2 2',
    check: 'm3 8 3 3 7-7',
    circle: 'M14 8A6 6 0 1 1 2 8a6 6 0 0 1 12 0',
    clock: 'M14 8A6 6 0 1 1 2 8a6 6 0 0 1 12 0 M8 4v4l3 2',
    block: 'M14 8A6 6 0 1 1 2 8a6 6 0 0 1 12 0 M4 4l8 8',
    close: 'm4 4 8 8 M12 4l-8 8',
    minus: 'M3 8h10',
    plus: 'M8 2v12 M2 8h12',
    chevron: 'm5 3 5 5-5 5',
    down: 'm3 5 5 5 5-5',
    theme: 'M8 2a6 6 0 1 0 6 6A6 6 0 0 1 8 2z',
    bell: 'M4 6a4 4 0 0 1 8 0v4l1 2H3l1-2z M7 14h2',
    archive: 'M2 3h12v3H2z M3 6v7h10V6',
    key: 'M8 5a3 3 0 1 1-6 0 3 3 0 0 1 6 0 M7 7l6 6 1-1',
    back: 'm10 3-5 5 5 5',
    bold: 'M4 2h4a3 3 0 0 1 0 6H4z M4 8h5a3 3 0 0 1 0 6H4z',
    italic: 'M7 2h6 M3 14h6 M10 2 6 14',
    strike: 'M12 4c-1-3-8-3-8 1 0 2 2 2 4 3s4 1 4 3c0 4-7 4-8 1 M2 8h12',
    code: 'm5 4-4 4 4 4 M11 4l4 4-4 4 M9 2 7 14',
    quote: 'M3 4h3v4H3z M6 8c0 3-1 4-3 4 M10 4h3v4h-3z M13 8c0 3-1 4-3 4',
    list: 'M3 4h.01 M6 4h7 M3 11h.01 M6 11h7',
    download: 'M8 2v8 M4 6l4 4 4-4 M2 11v3h12v-3',
  };
  const api = {};
  for (const [name, d] of Object.entries(paths)) api[name] = () => {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    for (const [key, value] of Object.entries({viewBox:'0 0 16 16', width:16, height:16, fill:'none', stroke:'currentColor', 'stroke-width':1.5, 'stroke-linecap':'round', 'stroke-linejoin':'round', 'aria-hidden':'true', focusable:'false', class:'icon'})) svg.setAttribute(key, value);
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('d', d); svg.append(path);
    if (name === 'agents') {
      const head = document.createElementNS(svg.namespaceURI, 'circle');
      for (const [key, value] of Object.entries({cx:8, cy:4, r:1.75, fill:'currentColor', stroke:'none'})) head.setAttribute(key, value);
      svg.append(head);
    }
    return svg;
  };
  api.status = (status) => (api[{'open':'circle','in-progress':'clock','done':'check','blocked':'block','awaiting-human':'attention','wontfix':'close'}[status]] || api.circle)();
  api.forAction = (text) => {
    const label = String(text).toLowerCase();
    const rules = [[/delete|remove|discard|reject/, 'trash'], [/revoke/, 'key'], [/archive/, 'archive'], [/create|new|add|register/, 'plus'], [/cancel|close/, 'close'], [/save|approve|resolve|mark.*read/, 'check'], [/send|comment|reply/, 'send'], [/search|find/, 'search'], [/export|download/, 'download'], [/edit|update/, 'edit'], [/refresh|retry|load/, 'context'], [/transfer|forward|open/, 'forward']];
    return (api[(rules.find(([re]) => re.test(label)) || [null, 'circle'])[1]])();
  };
  return Object.freeze(api);
})();
