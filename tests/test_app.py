"""The plugin's controller: what the tab may show, sending, and losing the pairing."""

import json
import os
import shutil
import subprocess
import pathlib
import stat
import sys
import tempfile
import unittest

from khayt_orca import app, pairing, tab
from khayt_orca.transport import ShopError

PAIRED = pairing.Paired(base="http://192.0.2.20:8765", device_id="dev_FakePluginDevice01",
                        key=pairing.b64(bytes(32)), mac_identity="")


class FakeShop:
    def __init__(self, base, paired):
        self.calls = FakeShop.calls

    calls = []
    queue_answer = None

    def announce(self, capture):
        self.calls.append("announce")
        return {"sliceId": "S" * 16, "state": "awaiting-file"}

    def upload(self, slice_id, path, sha):
        self.calls.append("upload")
        return {"state": "in-inbox"}

    def queue(self, etag=None):
        self.calls.append("queue " + str(etag))
        if isinstance(FakeShop.queue_answer, BaseException):
            raise FakeShop.queue_answer
        return FakeShop.queue_answer

    def unpair(self):
        self.calls.append("unpair")

    slice_states = []

    def slice_state(self, slice_id):
        self.calls.append("state " + slice_id)
        r = FakeShop.slice_states.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        FakeShop.calls, FakeShop.queue_answer, FakeShop.slice_states = [], None, []
        self.c = app.Controller(os.path.join(self.tmp.name, "store"), shop_factory=FakeShop)
        self.c.paired = PAIRED
        self.c._save_paired(PAIRED)

    def tearDown(self):
        self.c.stop()
        self.tmp.cleanup()

    def test_the_view_carries_only_allowlisted_fields(self):
        FakeShop.queue_answer = ({
            "v": 1, "shop": {"name": "Sample Print Shop"}, "inbox": {"waiting": 2},
            "jobs": [{"id": "J-1", "number": "J-1001", "stage": "printing", "machine": "U1-01", "file": None,
                      "title": "Keychains for Fatima", "clientName": "Fatima Alharbi"}],
            "machines": [{"id": "m_1", "name": "U1-01", "state": "active", "ip": "192.0.2.10"}],
        }, '"e1"')
        self.c.refresh_queue()
        v = self.c.view()
        self.assertEqual(v["shop"], "Sample Print Shop")
        self.assertEqual(v["jobs"], [{"number": "J-1001", "stage": "printing", "machine": "U1-01", "file": None}])
        self.assertEqual(v["machines"], [{"name": "U1-01", "state": "active"}])
        self.assertEqual(v["inboxWaiting"], 2)
        text = repr(v)
        for never in ("Fatima", "Keychains", "192.0.2.10", "clientName", "title"):
            self.assertNotIn(never, text)
        self.c.refresh_queue()
        self.assertEqual(FakeShop.calls[-1], 'queue "e1"', "asks with the ETag it has")

    def test_send_then_deliver(self):
        p = os.path.join(self.tmp.name, "Bracket_PLA.gcode")
        pathlib.Path(p).write_text("G28\n", encoding="utf-8")
        self.c.send(p, intent="quote")
        self.c.deliver()
        self.assertEqual(FakeShop.calls, ["announce", "upload"])
        self.assertEqual(self.c.view()["sent"], [{"file": "Bracket_PLA.gcode", "state": "done", "reason": None,
                                                  "shop": None}])

    def test_what_the_shop_did_with_a_sent_file_once_a_minute_until_settled(self):
        p = os.path.join(self.tmp.name, "Bracket_PLA.gcode")
        pathlib.Path(p).write_text("G28\n", encoding="utf-8")
        clock = [1_800_000_000.0]
        self.c.clock = lambda: clock[0]
        self.c.outbox.clock = self.c.clock
        self.c.send(p)
        self.c.deliver()
        FakeShop.slice_states = ["in-inbox", "left-inbox"]
        self.assertEqual(self.c.refresh_sent(), 1)
        self.assertEqual(self.c.view()["sent"][0]["shop"], "in-inbox")
        self.assertEqual(self.c.refresh_sent(), 0, "not again within the minute")
        clock[0] += 61
        self.assertEqual(self.c.refresh_sent(), 1)
        self.assertEqual(self.c.view()["sent"][0]["shop"], "left-inbox")
        clock[0] += 3600
        self.assertEqual(self.c.refresh_sent(), 0, "settled: never asked again")
        self.assertEqual([c for c in FakeShop.calls if c.startswith("state")], ["state " + "S" * 16] * 2)

    def test_a_slice_the_mac_forgot_is_gone_not_an_error(self):
        p = os.path.join(self.tmp.name, "Bracket_PLA.gcode")
        pathlib.Path(p).write_text("G28\n", encoding="utf-8")
        self.c.send(p)
        self.c.deliver()
        FakeShop.slice_states = [ShopError(404, "unknown-slice")]
        self.c.refresh_sent()
        self.assertEqual(self.c.view()["sent"][0]["shop"], "gone")

    def test_unpaired_by_the_shop_forgets_the_key(self):
        FakeShop.queue_answer = ShopError(401, "device-revoked")
        self.c.refresh_queue()
        self.assertIsNone(self.c.paired)
        self.assertFalse(os.path.exists(os.path.join(self.c.root, app.PAIRED_FILE)))
        self.assertEqual(self.c.view()["problem"], "unpaired")
        self.assertEqual(app.Controller(self.c.root, shop_factory=FakeShop).paired, None, "and after a restart")

    def test_a_mac_without_the_routes_says_update_khayt(self):
        FakeShop.queue_answer = ShopError(404, "error")
        self.c.refresh_queue()
        self.assertEqual(self.c.view()["problem"], "khayt-too-old")
        self.assertIsNotNone(self.c.paired, "still paired: the Mac is older, not gone")

    def test_updated_at_is_kept_when_a_refresh_fails(self):
        self.assertIsNone(self.c.view()["updatedAt"])
        FakeShop.queue_answer = ({"v": 1, "jobs": []}, "e1")
        self.c.refresh_queue()
        first = self.c.view()["updatedAt"]
        self.assertIsInstance(first, int)
        FakeShop.queue_answer = (None, "e1")          # 304: unchanged, still an answer
        self.c.refresh_queue()
        self.assertGreaterEqual(self.c.view()["updatedAt"], first)
        kept = self.c.view()["updatedAt"]
        FakeShop.queue_answer = OSError("no route")
        self.c.refresh_queue()
        v = self.c.view()
        self.assertEqual((v["problem"], v["updatedAt"]), ("offline", kept), "the tab shows the last good time, amber")

    def test_unpair_tells_the_mac_then_forgets(self):
        self.c.forget_pairing()
        self.assertEqual(FakeShop.calls, ["unpair"])
        self.assertIsNone(app.Controller(self.c.root, shop_factory=FakeShop).paired)

    @unittest.skipIf(sys.platform == "win32", "POSIX file modes")
    def test_the_key_file_is_the_users_alone(self):
        mode = stat.S_IMODE(os.stat(os.path.join(self.c.root, app.PAIRED_FILE)).st_mode)
        self.assertEqual(mode, 0o600)

    def test_kept_across_a_restart(self):
        again = app.Controller(self.c.root, shop_factory=FakeShop)
        self.assertEqual(again.paired, PAIRED)


class TabPage(unittest.TestCase):
    def test_the_page_writes_text_never_html(self):
        self.assertNotIn("innerHTML", tab.HTML)
        self.assertNotIn("insertAdjacentHTML", tab.HTML)
        self.assertNotIn("<script src", tab.HTML, "self-contained: no network of its own")
        self.assertIn("textContent", tab.HTML)
        self.assertNotIn("192.168.", tab.HTML)


@unittest.skipUnless(shutil.which("node"), "needs node")
class TabScript(unittest.TestCase):
    def test_the_page_runs_in_english_and_arabic(self):
        js = tab.HTML.split("<script>")[1].split("</script>")[0]
        out = subprocess.run(["node", str(pathlib.Path(__file__).parent / "tab_smoke.js")], input=js,
                             capture_output=True, text=True, encoding="utf-8", check=True,
                             env={**os.environ, "TZ": "UTC"}).stdout
        got = json.loads(out)
        self.assertEqual(got["en"]["first"], {"type": "ready"}, "the page asks for its data on load")
        self.assertEqual(got["en"]["dir"], "ltr")
        self.assertEqual(got["ar"]["dir"], "rtl")
        self.assertEqual(got["en"]["jobs"][0], "J-1001 | Bracket_PLA.gcode | U1-01 | Printing")
        # Figma page 20: a chip in the Mac's words, "—" for what is missing, a failed
        # send says why in words (never the code), and when the queue was last current.
        self.assertEqual(got["en"]["jobs"][1], "J-1003 | — | — | On Hold")
        self.assertEqual(got["en"]["chipClass"], "chip printing")
        self.assertEqual(got["en"]["sent"][1], "b.gcode | Not sent · not a G-code file")
        self.assertEqual(got["en"]["sent"][0], "c.gcode | Not sent · couldn't be sent")
        self.assertEqual(got["ar"]["sent"][1], "b.gcode | لم يُرسَل · ليس ملف G-code")
        self.assertEqual(got["ar"]["sent"][2], "a.gcode | أخذه المتجر")
        self.assertFalse(any("wrong-kind" in r or "weird-new-code" in r for lang in got.values() for r in lang["sent"]))
        self.assertEqual(got["en"]["fresh"], "Sample Print Shop · updated 11:02")
        self.assertEqual(got["ar"]["fresh"], "Sample Print Shop · حُدّث 11:02")
        self.assertEqual(got["en"]["stale"]["text"], "Couldn't reach the Mac · showing 11:02")
        self.assertTrue(got["en"]["stale"]["freshShown"] and got["en"]["stale"]["shopHidden"],
                        "the amber line takes the shop line's place")
        self.assertIn('id="fresh" hidden', tab.HTML.replace('class="sub warn" ', ''))
        self.assertTrue(got["en"]["stale"]["problemHidden"], "the amber line says it; not twice")
        self.assertEqual(got["en"]["check"], "418 207")

    def test_the_price_card(self):
        # Figma page 20 (186:237 … 186:371), approved: only the Mac's figures, formatted.
        js = tab.HTML.split("<script>")[1].split("</script>")[0]
        out = subprocess.run(["node", str(pathlib.Path(__file__).parent / "tab_smoke.js")], input=js,
                             capture_output=True, text=True, encoding="utf-8", check=True,
                             env={**os.environ, "TZ": "UTC"}).stdout
        en, ar = json.loads(out)["en"]["price"], json.loads(out)["ar"]["price"]
        self.assertTrue(en["off"]["hidden"], "off: no block at all, not a blank")
        self.assertTrue(en["unpaired"])
        self.assertEqual(en["on"]["rows"], ["Price | SAR 124.50", "3 × SAR 41.50 · rounded"])
        self.assertEqual(en["on"]["foot"], "Priced by the shop's Mac · some rates assumed")
        self.assertEqual(en["on"]["qty"], "3")
        self.assertEqual(en["cost"]["rows"][2:], ["Cost | SAR 84.30", "Margin | 32.3% of price"])
        self.assertEqual(en["costAmount"]["rows"][3], "Margin | SAR 40.20 · 32.3% of price")
        self.assertEqual(en["asDrawn"]["rows"], ["Price | SAR 124.50", "3 × SAR 41.50 · rounded to 0.50",
                                                 "Cost | SAR 84.30", "Margin | SAR 40.20 · 47.7% on cost"],
                         "186:261 as drawn")
        self.assertEqual(ar["asDrawn"]["rows"], ["السعر | 124.50 ر.س", "3 × 41.50 ر.س · مقرَّب إلى 0.50",
                                                 "التكلفة | 84.30 ر.س", "الهامش | 40.20 ر.س · 47.7% فوق التكلفة"],
                         "186:338 as drawn")
        self.assertEqual(en["one"]["rows"], ["Price | SAR 1,240.50"], "one piece, not rounded: no sub-line")
        self.assertEqual(en["one"]["foot"], "Priced by the shop's Mac")
        self.assertEqual(en["asking"]["rows"], ["Pricing…"])
        self.assertEqual(en["unreachable"]["rows"], ["Couldn't reach the Mac · no price shown | Retry"])
        self.assertIsNone(en["unreachable"]["foot"])
        self.assertEqual(en["noMaterial"]["rows"], ["Add a PETG spool in Khayt to price this"])
        self.assertEqual(en["retry"], {"posted": [{"type": "price-retry"}], "rows": ["Pricing…"]})
        # The stepper: the number moves at once, the Mac is asked once, 600 ms after the last click.
        self.assertEqual(en["stepping"], {"qty": "5", "rows": ["Pricing…"], "posted": 0, "kept": ["Pricing…"],
                                          "after": [{"type": "qty", "qty": 5}]})
        self.assertIn("600", tab.HTML)
        self.assertEqual(ar["on"]["rows"], ["السعر | 124.50 ر.س", "3 × 41.50 ر.س · مقرَّب"])
        self.assertEqual(ar["on"]["foot"], "سعّره Mac المتجر · بعض المعدّلات مفترضة")
        self.assertEqual(ar["cost"]["rows"][2:], ["التكلفة | 84.30 ر.س", "الهامش | 32.3% من السعر"])
        self.assertEqual(ar["unreachable"]["rows"], ["تعذّر الوصول إلى الـMac · لا يُعرض سعر | إعادة المحاولة"])
        for lang in (en, ar):
            self.assertFalse(any(c.isdigit() for r in lang["unreachable"]["rows"] for c in r),
                             "an unreachable Mac shows no number")

    def test_stage_and_machine_words_are_the_macs(self):
        # Copied from Khayt (renderer/locales/{en,ar}.js queue.*, Words.swift
        # mac.life_badge_*), so the queue reads the same in the tab as on the Mac.
        mac = {
            "en": {"pending": "Pending", "printing": "Printing", "post": "Post-Processing", "qc": "QC",
                   "on_hold": "On Hold", "service": "Under service", "retired": "Retired"},
            "ar": {"pending": "بانتظار", "printing": "قيد الطباعة", "post": "ما بعد الطباعة",
                   "qc": "مراقبة الجودة", "on_hold": "متوقف مؤقتاً", "service": "تحت الصيانة",
                   "retired": "خارج الخدمة"},
        }
        for lang, words in mac.items():
            block = tab.HTML.split("  " + lang + ": {")[1].split("\n  },")[0]
            for key, word in words.items():
                self.assertIn(key + ": '" + word + "'", block, lang + " " + key)

    def test_arabic_never_spells_khayt_in_latin(self):
        # Turki's ruling (Nov 10): in Arabic the name is خيط; the Mac's button is «إقران برنامج تقطيع».
        ar = tab.HTML.split("ar: {")[1].split("\n  },")[0]
        self.assertNotIn("Khayt", ar)
        self.assertNotIn("اقتران", ar, "the Mac's word is إقران")
        self.assertIn("إقران برنامج تقطيع", ar)
