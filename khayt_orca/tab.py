"""The Khayt tab's page: self-contained HTML, inline CSS and JS, no network of its own.

Everything shown arrives from the plugin as plain data (`Controller.view`) and is
written with textContent, never as HTML: a file name typed by a person is text.
"""

HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<style>
:root{--bg:#fff;--card:#f6f8f8;--line:#e3e6e8;--t1:#101417;--t2:#697079;--brand:#216dca;--warn:#b26b00;
  /* The Mac's status tokens (Figma Qqxg4imtKVgC3TfNGcRGOk, page 20) */
  --live:#1bb55c;--live-w:#e3f6ea;--pend:#5e6c84;--pend-w:#edf0f4;--hold:#8a6fd1;--wash:#eef4fc}
@media (prefers-color-scheme:dark){:root{--bg:#16191c;--card:#1f2327;--line:#2c3136;--t1:#eef1f3;--t2:#9aa3ab;--brand:#4b8fe0;--warn:#e0a040;
  --live:#34d27a;--live-w:#0f3a27;--pend:#a9b8cf;--pend-w:#3a4658;--hold:#b49cff;--wash:#12345e}}
body{margin:0;padding:16px;background:var(--bg);color:var(--t1);font:13px/1.45 -apple-system,system-ui,Segoe UI,sans-serif}
h1{font-size:18px;margin:0 0 4px} .sub{color:var(--t2);margin:0 0 14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:0 0 14px;overflow:hidden}
.card h2{font-size:13px;margin:0;padding:10px 14px;border-bottom:1px solid var(--line)}
.row{display:flex;gap:10px;padding:8px 14px;border-top:1px solid var(--line)} .row:first-of-type{border-top:0}
.row .grow{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.muted{color:var(--t2)} .warn{color:var(--warn)} .mono{font-family:ui-monospace,Menlo,monospace}
.chip{display:inline-flex;align-items:center;gap:5px;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:500;white-space:nowrap;color:var(--brand);background:var(--wash)}
.chip::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}
.chip.printing{color:var(--live);background:var(--live-w)} .chip.pending{color:var(--pend);background:var(--pend-w)} .chip.on_hold{color:var(--hold)}
#fresh{font-size:12px}
button{font:inherit;padding:5px 12px;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--t1);cursor:pointer}
button.primary{background:var(--brand);border-color:var(--brand);color:#fff}
input{font:inherit;padding:5px 8px;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--t1);width:220px}
.check{font:600 28px ui-monospace,Menlo,monospace;letter-spacing:4px;margin:8px 0}
[hidden]{display:none}
</style></head><body>
<h1 data-w="brand"></h1><p class="sub" id="shop" data-w="tagline"></p><p class="sub warn" id="fresh" hidden></p>
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
    refreshing: 'Refreshing…', updated: (t) => 'updated ' + t, stale: (t) => "Couldn't reach the Mac · showing " + t,
    why: {'wrong-kind': 'not a G-code file', 'too-large': "too large for the shop's inbox", 'inbox-full': "the shop's inbox is full",
      'hash-mismatch': 'arrived damaged; send it again', 'gave-up': "the Mac didn't answer; send it again",
      'unknown-slice': 'the shop no longer expected this file', 'not-awaiting-file': 'the shop no longer expected this file',
      'device-revoked': 'this computer was unpaired', 'device-unknown': 'this computer was unpaired', other: "couldn't be sent"},
    inInbox: (n) => '· ' + n + ' in the inbox', noJobs: 'No open jobs.', more: (n) => 'and ' + n + ' more',
    problem: 'Khayt: ', pairing: 'Pairing: ', 'khayt-too-old': 'Update Khayt on the Mac: this version has no slicer routes.',
    pair_permission: 'OrcaSlicer needs your OK to reach the Mac. Allow it, then click Pair again.',
    pair_offline: 'The Mac did not answer. Check the address and that Khayt is open, then click Pair again.',
    pair_address: 'Type the address the Mac shows, like 192.0.2.20:3219.',
    new: 'Waiting to send', announced: 'Sending…', done: 'In the Khayt inbox', failed: 'Not sent',
    'in-inbox': 'In the Khayt inbox', 'left-inbox': 'Taken by the shop', expired: 'No longer in the inbox', gone: 'No longer in the inbox',
    // The Mac's own words (Khayt queue.* and mac.life_badge_*): tests/test_app.py holds them equal.
    stage: {pending: 'Pending', printing: 'Printing', post: 'Post-Processing', qc: 'QC', on_hold: 'On Hold'},
    machine: {active: 'Ready', service: 'Under service', retired: 'Retired'}},
  ar: {brand: 'خيط', tagline: 'أرسل الملفات المقطّعة إلى صندوق متجرك.', pairTitle: 'الإقران بمتجرك',
    pairHow: 'في تطبيق خيط على الـMac اضغط «إقران برنامج تقطيع»، ثم أدخل العنوان الذي يظهر.', pair: 'إقران',
    checkHow: 'تأكد أن الـMac يعرض الرقم نفسه، ثم وافق هناك:', queue: 'قائمة أعمال المتجر',
    machines: 'الآلات', sent: 'المُرسَل من هذا الجهاز', refresh: 'تحديث', unpair: 'إلغاء الإقران',
    refreshing: 'جارٍ التحديث…', updated: (t) => 'حُدّث ' + t, stale: (t) => 'تعذّر الوصول إلى الـMac · المعروض من ' + t,
    why: {'wrong-kind': 'ليس ملف G-code', 'too-large': 'أكبر مما يقبله صندوق المتجر', 'inbox-full': 'صندوق المتجر ممتلئ',
      'hash-mismatch': 'وصل تالفًا، أرسله مرة أخرى', 'gave-up': 'لم يردّ الـMac، أرسله مرة أخرى',
      'unknown-slice': 'لم يعد المتجر ينتظر هذا الملف', 'not-awaiting-file': 'لم يعد المتجر ينتظر هذا الملف',
      'device-revoked': 'أُلغي إقران هذا الجهاز', 'device-unknown': 'أُلغي إقران هذا الجهاز', other: 'تعذّر الإرسال'},
    inInbox: (n) => '· ' + n + ' في الصندوق', noJobs: 'لا توجد أعمال مفتوحة.', more: (n) => 'و' + n + ' غيرها',
    problem: 'خيط: ', pairing: 'الإقران: ', 'khayt-too-old': 'حدّث تطبيق خيط على الـMac: هذا الإصدار لا يستقبل برامج التقطيع.',
    pair_permission: 'يحتاج OrcaSlicer موافقتك للوصول إلى الـMac. اسمح بذلك ثم اضغط «إقران» مرة أخرى.',
    pair_offline: 'لم يردّ الـMac. تحقّق من العنوان ومن أن تطبيق خيط مفتوح، ثم اضغط «إقران» مرة أخرى.',
    pair_address: 'اكتب العنوان الذي يعرضه الـMac، مثل 192.0.2.20:3219.',
    new: 'بانتظار الإرسال', announced: 'جارٍ الإرسال…', done: 'في صندوق خيط', failed: 'لم يُرسَل',
    'in-inbox': 'في صندوق خيط', 'left-inbox': 'أخذه المتجر', expired: 'لم يعد في الصندوق', gone: 'لم يعد في الصندوق',
    stage: {pending: 'بانتظار', printing: 'قيد الطباعة', post: 'ما بعد الطباعة', qc: 'مراقبة الجودة', on_hold: 'متوقف مؤقتاً'},
    machine: {active: 'جاهزة', service: 'تحت الصيانة', retired: 'خارج الخدمة'}},
};
const W = (navigator.language || 'en').toLowerCase().startsWith('ar') ? WORDS.ar : WORDS.en;
if (W === WORDS.ar) { document.documentElement.lang = 'ar'; document.documentElement.dir = 'rtl'; }
for (const el of document.querySelectorAll('[data-w]')) el.textContent = W[el.dataset.w];

function row(parent, cells) {
  const r = document.createElement('div'); r.className = 'row';
  for (const cell of cells) {
    if (cell && cell.nodeType) { r.appendChild(cell); continue; }
    const [text, cls] = cell;
    const s = document.createElement('div'); s.className = cls || ''; s.textContent = text == null ? '' : String(text); r.appendChild(s);
  }
  parent.appendChild(r);
}
function draw(v) {
  $('pairCard').hidden = v.paired; $('actions').hidden = !v.paired;
  $('queueCard').hidden = !v.paired; $('machinesCard').hidden = !v.paired || !v.machines.length;
  $('sentCard').hidden = !v.sent.length;
  // When the queue is current: "updated 14:02". When a refresh failed: amber, and the
  // last good queue stays on screen (Figma page 20, frame B). Latin digits in both languages.
  const at = v.updatedAt ? new Date(v.updatedAt).toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit'}) : null;
  const stale = !!(v.paired && v.problem && at);
  // Current: one line, "Sample Print Shop · updated 14:02". Stale: the amber line
  // takes its place (frame B).
  $('shop').textContent = (v.shop ? v.shop : W.tagline) + (at && !stale ? ' · ' + W.updated(at) : '');
  $('shop').hidden = stale; $('fresh').hidden = !stale;
  $('fresh').textContent = stale ? W.stale(at) : '';
  $('problem').hidden = !v.problem || stale; $('problem').textContent = v.problem ? (W[v.problem] || W.problem + v.problem) : '';
  $('refresh').disabled = false; $('refresh').textContent = W.refresh;
  $('inbox').textContent = v.inboxWaiting != null ? W.inInbox(v.inboxWaiting) : '';
  $('jobs').replaceChildren(); $('machines').replaceChildren(); $('sent').replaceChildren();
  for (const j of v.jobs) {
    const chip = document.createElement('span'); chip.className = 'chip ' + (W.stage[j.stage] ? j.stage : '');
    chip.textContent = W.stage[j.stage] || j.stage;
    row($('jobs'), [[j.number, 'mono'], [j.file || '—', 'grow muted'], [j.machine || '—', 'muted'], chip]);
  }
  if (!v.jobs.length) row($('jobs'), [[W.noJobs, 'muted grow']]);
  if (v.more) row($('jobs'), [[W.more(v.more), 'muted grow']]);
  for (const m of v.machines) row($('machines'), [[m.name, 'grow'], [W.machine[m.state] || m.state, m.state === 'service' ? 'warn' : 'muted']]);
  for (const s of v.sent.slice().reverse()) {
    const said = s.state === 'done' ? (W[s.shop] || W.done) : (W[s.state] || s.state);
    const failed = s.state === 'failed';
    const why = failed ? (W.why[s.reason] || W.why.other) : '';
    row($('sent'), [[s.file, 'grow mono'], [failed ? said + ' · ' + why : said, failed ? 'warn' : 'muted']]);
  }
}
orca.onMessage((m) => {
  if (m.type === 'view') draw(m.view);
  if (m.type === 'check') { $('checkRow').hidden = false; $('check').textContent = m.check; }
  if (m.type === 'paired' || m.type === 'pair-failed') { $('checkRow').hidden = true; $('pairBtn').disabled = false; }
  if (m.type === 'pair-failed') { $('problem').hidden = false; $('problem').textContent = W['pair_' + m.reason] || (W.pairing + m.reason); }
});
$('pairBtn').onclick = () => { $('pairBtn').disabled = true; orca.postMessage({type: 'pair', address: $('addr').value, name: 'OrcaSlicer'}); };
$('refresh').onclick = () => { $('refresh').disabled = true; $('refresh').textContent = W.refreshing; orca.postMessage({type: 'refresh'}); };
$('unpair').onclick = () => orca.postMessage({type: 'unpair'});
document.addEventListener('visibilitychange', () => orca.postMessage({type: document.hidden ? 'hidden' : 'ready'}));
orca.postMessage({type: 'ready'});
</script></body></html>
"""
