// Runs the Khayt tab's script against a stub DOM: no browser needed.
const fs = require('fs');
// The page's script, from tab.py (passed on stdin by tests/test_app.py).
const src = fs.readFileSync(0, 'utf8');
function run(lang) {
  const els = {};
  const mk = (id) => ({ id, nodeType: 1, hidden: false, textContent: '', className: '', dataset: {}, disabled: false, children: [],
    appendChild(c) { this.children.push(c); }, replaceChildren() { this.children = []; }, onclick: null });
  const ids = ['shop','fresh','problem','pairCard','addr','pairBtn','checkRow','check','queueCard','inbox','jobs','machinesCard','machines','sentCard','sent','actions','refresh','unpair','priceCard','qty','qMinus','qPlus','priceBody','priceFoot'];
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
  const timers = [];
  const fakeSetTimeout = (fn) => { timers.push(fn); return timers.length; };
  const fakeClearTimeout = (i) => { if (i) timers[i - 1] = null; };
  new Function('document', 'navigator', 'orca', 'setTimeout', 'clearTimeout', src)(ctx.document, ctx.navigator, ctx.orca, fakeSetTimeout, fakeClearTimeout);
  const firstPosted = posted[0];
  // The price card, one state at a time (sample figures only).
  const base = { paired: true, shop: 'Sample Print Shop', jobs: [], more: 0, machines: [], inboxWaiting: 0, sent: [], problem: null, updatedAt: null };
  const text = (el) => el.children.map((r) => r.children.map((c) => c.textContent).join(' | '));
  const card = (price) => {
    handler({ type: 'view', view: { ...base, price } });
    return { hidden: els.priceCard.hidden, rows: text(els.priceBody), foot: els.priceFoot.hidden ? null : els.priceFoot.textContent, qty: els.qty.textContent };
  };
  const on = { state: 'on', qty: 3, currency: 'SAR', unit: 41.5, total: 124.5, rounded: true, basis: 'assumed' };
  const price = {
    off: card(null),
    unpaired: (() => { handler({ type: 'view', view: { ...base, paired: false, price: on } }); return els.priceCard.hidden; })(),
    on: card(on),
    cost: card({ ...on, cost: { unit: 28.1, total: 84.3 }, margin: { pct: 32.29 } }),
    costAmount: card({ ...on, cost: { unit: 28.1, total: 84.3 }, margin: { pct: 32.29, amount: 40.2 } }),
    one: card({ ...on, qty: 1, unit: 1240.5, total: 1240.5, rounded: false, basis: 'measured' }),
    asking: card({ state: 'asking', qty: 4 }),
    unreachable: card({ state: 'unreachable', qty: 3 }),
    noMaterial: card({ state: 'unpriced', reason: 'no-material', qty: 3, type: 'PETG' }),
    noCurrency: card({ state: 'unpriced', reason: 'no-currency', qty: 3 }),
  };
  card({ state: 'unreachable', qty: 3 }); posted.length = 0;
  els.priceBody.children[0].children[1].onclick();            // Retry
  price.retry = { posted: posted.slice(), rows: text(els.priceBody) };
  card(on); posted.length = 0;
  els.qPlus.onclick(); els.qPlus.onclick();
  price.stepping = { qty: els.qty.textContent, rows: text(els.priceBody), posted: posted.length };
  card(on);                                                    // a view while choosing keeps "Pricing…"
  price.stepping.kept = text(els.priceBody);
  for (const t of timers) if (t) t();
  price.stepping.after = posted.slice();
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
  return { fresh, stale, chipClass: els.jobs.children[0].children[3].className, dir: ctx.document.documentElement.dir || 'ltr', first: firstPosted, tagline: wEls[0].textContent,
    sent: els.sent.children.map((r) => r.children.map((c) => c.textContent).join(' | ')),
    price, jobs: els.jobs.children.map((r) => r.children.map((c) => c.textContent).join(' | ')), check: els.check.textContent };
}
process.stdout.write(JSON.stringify({ en: run('en-GB'), ar: run('ar-SA') }));
