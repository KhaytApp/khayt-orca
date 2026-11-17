"""The Khayt tab's page: self-contained HTML, inline CSS and JS, no network of its own.

Everything shown arrives from the plugin as plain data (`Controller.view`) and is
written with textContent, never as HTML: a file name typed by a person is text.
"""

HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<style>
:root{--bg:#fff;--card:#f6f8f8;--line:#e3e6e8;--t1:#101417;--t2:#697079;--brand:#216dca;--warn:#b26b00}
@media (prefers-color-scheme:dark){:root{--bg:#16191c;--card:#1f2327;--line:#2c3136;--t1:#eef1f3;--t2:#9aa3ab;--brand:#4b8fe0;--warn:#e0a040}}
body{margin:0;padding:16px;background:var(--bg);color:var(--t1);font:13px/1.45 -apple-system,system-ui,Segoe UI,sans-serif}
h1{font-size:18px;margin:0 0 4px} .sub{color:var(--t2);margin:0 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:0 0 14px;overflow:hidden}
.card h2{font-size:13px;margin:0;padding:10px 14px;border-bottom:1px solid var(--line)}
.row{display:flex;gap:10px;padding:8px 14px;border-top:1px solid var(--line)} .row:first-of-type{border-top:0}
.row .grow{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.muted{color:var(--t2)} .warn{color:var(--warn)} .mono{font-family:ui-monospace,Menlo,monospace}
button{font:inherit;padding:5px 12px;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--t1);cursor:pointer}
button.primary{background:var(--brand);border-color:var(--brand);color:#fff}
input{font:inherit;padding:5px 8px;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--t1);width:220px}
.check{font:600 28px ui-monospace,Menlo,monospace;letter-spacing:4px;margin:8px 0}
[hidden]{display:none}
</style></head><body>
<h1 data-w="brand"></h1><p class="sub" id="shop" data-w="tagline"></p>
<p class="warn" id="problem" hidden></p>

<div class="card" id="pairCard" hidden><h2 data-w="pairTitle"></h2><div class="row" style="flex-wrap:wrap">
  <div class="grow muted" style="white-space:normal" data-w="pairHow"></div>
  <input id="addr" placeholder="192.0.2.20:8765" autocomplete="off"><button class="primary" id="pairBtn" data-w="pair"></button>
</div><div class="row" id="checkRow" hidden><div class="grow"><span data-w="checkHow"></span>
  <div class="check" id="check"></div></div></div></div>

<div class="card" id="queueCard" hidden><h2><span data-w="queue"></span> <span class="muted" id="inbox"></span></h2><div id="jobs"></div></div>
<div class="card" id="machinesCard" hidden><h2 data-w="machines"></h2><div id="machines"></div></div>
<div class="card" id="sentCard" hidden><h2 data-w="sent"></h2><div id="sent"></div></div>
<p id="actions" hidden><button id="refresh" data-w="refresh"></button> <button id="unpair" data-w="unpair"></button></p>

<script>
const $ = (id) => document.getElementById(id);
const WORDS = {
  en: {brand: 'Khayt', tagline: "Send sliced files to your shop's inbox.", pairTitle: 'Pair with your shop',
    pairHow: 'In Khayt on the Mac, click Pair a slicer, then enter the address it shows.', pair: 'Pair',
    checkHow: 'Check that the Mac shows the same number, then approve there:', queue: 'Shop queue',
    machines: 'Machines', sent: 'Sent from this computer', refresh: 'Refresh', unpair: 'Unpair',
    inInbox: (n) => '· ' + n + ' in the inbox', noJobs: 'No open jobs.', more: (n) => 'and ' + n + ' more',
    problem: 'Khayt: ', pairing: 'Pairing: ', 'khayt-too-old': 'Update Khayt on the Mac: this version has no slicer routes.',
    pair_permission: 'OrcaSlicer needs your OK to reach the Mac. Allow it, then click Pair again.',
    pair_offline: 'The Mac did not answer. Check the address and that Khayt is open, then click Pair again.',
    pair_address: 'Type the address the Mac shows, like 192.0.2.20:3219.',
    new: 'Waiting to send', announced: 'Sending…', done: 'In the Khayt inbox', failed: 'Not sent',
    'in-inbox': 'In the Khayt inbox', 'left-inbox': 'Taken by the shop', expired: 'No longer in the inbox', gone: 'No longer in the inbox',
    stage: {pending: 'Waiting', printing: 'Printing', post: 'Finishing', qc: 'Checking', on_hold: 'On hold'},
    machine: {active: 'Ready', service: 'In service', retired: 'Retired'}},
  ar: {brand: 'خيط', tagline: 'أرسل الملفات المقطّعة إلى صندوق متجرك.', pairTitle: 'الإقران بمتجرك',
    pairHow: 'في تطبيق خيط على الـMac اضغط «إقران برنامج تقطيع»، ثم أدخل العنوان الذي يظهر.', pair: 'إقران',
    checkHow: 'تأكد أن الـMac يعرض الرقم نفسه، ثم وافق هناك:', queue: 'قائمة أعمال المتجر',
    machines: 'الآلات', sent: 'المُرسَل من هذا الجهاز', refresh: 'تحديث', unpair: 'إلغاء الإقران',
    inInbox: (n) => '· ' + n + ' في الصندوق', noJobs: 'لا توجد أعمال مفتوحة.', more: (n) => 'و' + n + ' غيرها',
    problem: 'خيط: ', pairing: 'الإقران: ', 'khayt-too-old': 'حدّث تطبيق خيط على الـMac: هذا الإصدار لا يستقبل برامج التقطيع.',
    pair_permission: 'يحتاج OrcaSlicer موافقتك للوصول إلى الـMac. اسمح بذلك ثم اضغط «إقران» مرة أخرى.',
    pair_offline: 'لم يردّ الـMac. تحقّق من العنوان ومن أن تطبيق خيط مفتوح، ثم اضغط «إقران» مرة أخرى.',
    pair_address: 'اكتب العنوان الذي يعرضه الـMac، مثل 192.0.2.20:3219.',
    new: 'بانتظار الإرسال', announced: 'جارٍ الإرسال…', done: 'في صندوق خيط', failed: 'لم يُرسَل',
    'in-inbox': 'في صندوق خيط', 'left-inbox': 'أخذه المتجر', expired: 'لم يعد في الصندوق', gone: 'لم يعد في الصندوق',
    stage: {pending: 'بالانتظار', printing: 'قيد الطباعة', post: 'التشطيب', qc: 'الفحص', on_hold: 'معلّق'},
    machine: {active: 'جاهزة', service: 'في الصيانة', retired: 'متقاعدة'}},
};
const W = (navigator.language || 'en').toLowerCase().startsWith('ar') ? WORDS.ar : WORDS.en;
if (W === WORDS.ar) { document.documentElement.lang = 'ar'; document.documentElement.dir = 'rtl'; }
for (const el of document.querySelectorAll('[data-w]')) el.textContent = W[el.dataset.w];

function row(parent, cells) {
  const r = document.createElement('div'); r.className = 'row';
  for (const [text, cls] of cells) { const s = document.createElement('div'); s.className = cls || ''; s.textContent = text == null ? '' : String(text); r.appendChild(s); }
  parent.appendChild(r);
}
function draw(v) {
  $('pairCard').hidden = v.paired; $('actions').hidden = !v.paired;
  $('queueCard').hidden = !v.paired; $('machinesCard').hidden = !v.paired || !v.machines.length;
  $('sentCard').hidden = !v.sent.length;
  $('shop').textContent = v.shop ? v.shop : W.tagline;
  $('problem').hidden = !v.problem; $('problem').textContent = v.problem ? (W[v.problem] || W.problem + v.problem) : '';
  $('inbox').textContent = v.inboxWaiting != null ? W.inInbox(v.inboxWaiting) : '';
  $('jobs').replaceChildren(); $('machines').replaceChildren(); $('sent').replaceChildren();
  for (const j of v.jobs) row($('jobs'), [[j.number, 'mono'], [j.file || '', 'grow muted'], [j.machine || '', 'muted'], [W.stage[j.stage] || j.stage, '']]);
  if (!v.jobs.length) row($('jobs'), [[W.noJobs, 'muted grow']]);
  if (v.more) row($('jobs'), [[W.more(v.more), 'muted grow']]);
  for (const m of v.machines) row($('machines'), [[m.name, 'grow'], [W.machine[m.state] || m.state, 'muted']]);
  for (const s of v.sent.slice().reverse()) {
    const said = s.state === 'done' ? (W[s.shop] || W.done) : (W[s.state] || s.state);
    row($('sent'), [[s.file, 'grow mono'], [said, s.state === 'failed' ? 'warn' : 'muted'], [s.reason || '', 'muted']]);
  }
}
orca.onMessage((m) => {
  if (m.type === 'view') draw(m.view);
  if (m.type === 'check') { $('checkRow').hidden = false; $('check').textContent = m.check; }
  if (m.type === 'paired' || m.type === 'pair-failed') { $('checkRow').hidden = true; $('pairBtn').disabled = false; }
  if (m.type === 'pair-failed') { $('problem').hidden = false; $('problem').textContent = W['pair_' + m.reason] || (W.pairing + m.reason); }
});
$('pairBtn').onclick = () => { $('pairBtn').disabled = true; orca.postMessage({type: 'pair', address: $('addr').value, name: 'OrcaSlicer'}); };
$('refresh').onclick = () => orca.postMessage({type: 'refresh'});
$('unpair').onclick = () => orca.postMessage({type: 'unpair'});
document.addEventListener('visibilitychange', () => orca.postMessage({type: document.hidden ? 'hidden' : 'ready'}));
orca.postMessage({type: 'ready'});
</script></body></html>
"""
