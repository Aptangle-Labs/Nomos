/* Optional runtime/interaction regression: NODE_PATH=data/dashboard-qa/node_modules
   node tests/dashboard_ui_checks.cjs http://127.0.0.1:8487 1
   Uses jsdom; this is DOM execution, not browser visual/layout verification. */
const assert = require('node:assert/strict');
const {JSDOM, VirtualConsole} = require('jsdom');
const base = process.argv[2] || 'http://127.0.0.1:8487';
const pid = process.argv[3] || '1';
const errors = [], calls = [], scheduled = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', e => errors.push(e.message));
virtualConsole.on('error', (...args) => errors.push(args.join(' ')));
let resize;
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function waitFor(fn) {
  for(let i=0;i<100;i++) {if(fn()) return; await sleep(20);}
  throw new Error('Timed out waiting for dashboard');
}
(async () => {
  const dom = await JSDOM.fromURL(`${base}/#/p/${pid}/dashboard`, {
    runScripts:'dangerously',resources:'usable',pretendToBeVisual:true,virtualConsole,
    beforeParse(window) {
      window.fetch = (url, options) => {calls.push(String(url));return fetch(new URL(url,base),options);};
      window.EventSource = class {addEventListener() {} close() {}};
      window.ResizeObserver = class {constructor(fn) {resize=fn;} observe() {} disconnect() {}};
      const timeout=window.setTimeout.bind(window);
      window.setTimeout=(fn,delay,...args) => {
        if(delay===3000) {scheduled.push(fn);return timeout(()=>{},3000);}
        return timeout(fn,delay,...args);
      };
    }
  });
  const {window}=dom, d=window.document;
  await waitFor(() => d.querySelectorAll('.dash-card').length===6);
  window.eval('window.testAC=AC;');
  const AC=window.testAC;
  assert.equal(d.querySelectorAll('.dash-tile').length,6);
  const tabs=[...d.querySelectorAll('#project-nav a')].map(a=>a.textContent);
  assert.equal(tabs[tabs.indexOf('Audit')+1],'Dashboard');
  assert.equal(d.querySelectorAll('.dash-ranges button').length,4);
  assert.equal(d.querySelectorAll('.dash-card polyline').length,4);
  assert.equal(d.querySelectorAll('.dash-card .dash-crosshair').length,1);
  assert.ok(!d.querySelector('svg').outerHTML.match(/NaN|undefined/));
  const colors=[...d.querySelectorAll('polyline')].map(p=>p.getAttribute('stroke'));
  const lineTarget=d.querySelector('.dash-card svg rect[aria-label^="Activity"]');
  lineTarget.dispatchEvent(new window.FocusEvent('focus'));
  assert.ok(!d.querySelector('.dash-tooltip').classList.contains('hidden'));
  lineTarget.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Home',bubbles:true}));
  assert.match(lineTarget.getAttribute('aria-label'),/messages/);
  lineTarget.dispatchEvent(new window.FocusEvent('blur'));
  assert.ok(d.querySelector('.dash-tooltip').classList.contains('hidden'));
  for(const button of [...d.querySelectorAll('.dash-card button')]) {
    button.click(); assert.equal(button.getAttribute('aria-pressed'),'true');
    assert.ok(button.closest('section').querySelector('table')); button.click();
  }
  d.querySelector('.dash-card button').click();
  const previous=calls.filter(c=>c.includes('/dashboard?')).length;
  AC.handleEvent('audit',{id:987654,actor:'vega'});
  AC.handleEvent('ticket_updated',{number:1});
  assert.equal(scheduled.length,2);
  assert.equal(calls.filter(c=>c.includes('/dashboard?')).length,previous);
  scheduled.at(-1)();
  await waitFor(()=>calls.filter(c=>c.includes('/dashboard?')).length===previous+1);
  await sleep(50);
  assert.ok(d.querySelector('.dash-card table'),'table state survives refresh');
  d.querySelector('.dash-card button').click();
  for(const button of [...d.querySelectorAll('.dash-range')]) {
    button.click(); await waitFor(()=>d.querySelectorAll('.dash-card').length===6);
    assert.deepEqual([...d.querySelectorAll('polyline')].map(p=>p.getAttribute('stroke')),colors);
  }
  for(const theme of ['light','dark']) {
    d.documentElement.dataset.theme=theme;
    const style=window.getComputedStyle(d.documentElement);
    ['#4285c2','#bd831d','#37a05e','#9a6fd0'].forEach((value,i)=>assert.equal(style.getPropertyValue(`--chart-${i+1}`).trim(),value));
  }
  resize([{contentRect:{width:900}}]);
  assert.equal(d.querySelectorAll('.dash-card').length,6);
  // Older responses may not overwrite a newer range or a departed view.
  const realReq=window._req, pending=[];
  window._req=(method,url,...args)=>url.includes('/dashboard?')?new Promise((resolve,reject)=>pending.push({resolve,reject,url})):realReq(method,url,...args);
  d.querySelectorAll('.dash-range')[0].click();
  d.querySelectorAll('.dash-range')[1].click();
  assert.equal(pending.length,2);
  const fixture=await (await fetch(`${base}/api/projects/${pid}/dashboard?range=7d`)).json();
  pending[1].resolve(fixture.data);await sleep(20);
  pending[0].resolve({...fixture.data,tiles:{...fixture.data.tiles,tickets_open:99999}});await sleep(20);
  assert.ok(!d.querySelector('.dash-tiles').textContent.includes('99,999'));
  d.querySelector('.dash-page > .spread button').click(); pending.at(-1).reject(new Error('fixture outage'));await sleep(20);
  assert.match(d.querySelector('[role=status]').textContent,/Showing stale data/);
  assert.equal(d.querySelectorAll('.dash-card').length,6);
  AC._busGen++;d.querySelector('.dash-page > .spread button').click();
  assert.equal(pending.length,3,'no request after leaving view generation');
  assert.deepEqual(errors,[]);
  console.log('PASS: seeded dashboard, tab order, six cards, SVG marks, keyboard hover, all tables, range colors, both theme tokens, debounce, resize callback, stale requests, error recovery; zero DOM runtime/console errors.');
  dom.window.close();
})().catch(error=>{console.error(error);process.exitCode=1;});
