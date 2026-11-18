// Runs the Khayt tab's script against a stub DOM: no browser needed.
const fs = require('fs');
// The page's script, from tab.py (passed on stdin by tests/test_app.py).
const src = fs.readFileSync(0, 'utf8');
function run(lang) {
  const els = {};
  const mk = (id) => ({ id, nodeType: 1, hidden: false, textContent: '', className: '', dataset: {}, disabled: false, children: [],
    appendChild(c) { this.children.push(c); }, replaceChildren() { this.children = []; }, onclick: null });
  const ids = ['shop','fresh','problem','pairCard','addr','pairBtn','checkRow','check','queueCard','inbox','jobs','machinesCard','machines','sentCard','sent','actions','refresh','unpair'];
  for (const id of ids) els[id] = mk(id);
  const wkeys = { tagline: 'shop', pairTitle: 'pairCard', pairHow: 'addr', pair: 'pairBtn', checkHow: 'check', queue: 'inbox', machines: 'machines', sent: 'sent', refresh: 'refresh', unpair: 'unpair' };
  const wEls = Object.keys(wkeys).map((k) => ({ dataset: { w: k }, textContent: '' }));
  let handler; const posted = [];
  const ctx = {
    document: { getElementById: (id) => els[id], createElement: () => mk('x'), querySelectorAll: () => wEls,
      documentElement: {}, addEventListener() {}, hidden: false },
    navigator: { language: lang },
    orca: { onMessage: (cb) => { handler = cb; }, postMessage: (m) => posted.push(m) },
  };
  new Function('document', 'navigator', 'orca', src)(ctx.document, ctx.navigator, ctx.orca);
  handler({ type: 'view', view: { paired: true, shop: 'Sample Print Shop', jobs: [{ number: 'J-1001', stage: 'printing', machine: 'U1-01', file: 'Bracket_PLA.gcode' }],
    more: 3, machines: [{ name: 'U1-01', state: 'active' }], inboxWaiting: 2,
    sent: [{ file: 'a.gcode', state: 'done', reason: null, shop: 'left-inbox' }, { file: 'b.gcode', state: 'failed', reason: 'wrong-kind', shop: null }, { file: 'c.gcode', state: 'failed', reason: 'weird-new-code', shop: null }], problem: null, updatedAt: Date.UTC(2026, 10, 19, 11, 2) } });
  const fresh = els.shop.textContent;
  handler({ type: 'view', view: { paired: true, shop: 'Sample Print Shop', jobs: [], more: 0, machines: [], inboxWaiting: 0, sent: [], problem: 'offline', updatedAt: Date.UTC(2026, 10, 19, 11, 2) } });
  const stale = { text: els.fresh.textContent, freshShown: !els.fresh.hidden, shopHidden: els.shop.hidden, problemHidden: els.problem.hidden };
  handler({ type: 'view', view: { paired: true, shop: 'Sample Print Shop', jobs: [{ number: 'J-1001', stage: 'printing', machine: 'U1-01', file: 'Bracket_PLA.gcode' }, { number: 'J-1003', stage: 'on_hold', machine: null, file: null }],
    more: 3, machines: [{ name: 'U1-01', state: 'active' }], inboxWaiting: 2,
    sent: [{ file: 'a.gcode', state: 'done', reason: null, shop: 'left-inbox' }, { file: 'b.gcode', state: 'failed', reason: 'wrong-kind', shop: null }, { file: 'c.gcode', state: 'failed', reason: 'weird-new-code', shop: null }], problem: null, updatedAt: Date.UTC(2026, 10, 19, 11, 2) } });
  handler({ type: 'check', check: '418 207' });
  return { fresh, stale, chipClass: els.jobs.children[0].children[3].className, dir: ctx.document.documentElement.dir || 'ltr', first: posted[0], tagline: wEls[0].textContent,
    sent: els.sent.children.map((r) => r.children.map((c) => c.textContent).join(' | ')),
    jobs: els.jobs.children.map((r) => r.children.map((c) => c.textContent).join(' | ')), check: els.check.textContent };
}
process.stdout.write(JSON.stringify({ en: run('en-GB'), ar: run('ar-SA') }));
