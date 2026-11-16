"""The plugin's controller: what the tab may show, sending, and losing the pairing."""

import os
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


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        FakeShop.calls, FakeShop.queue_answer = [], None
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
        self.assertEqual(self.c.view()["sent"], [{"file": "Bracket_PLA.gcode", "state": "done", "reason": None}])

    def test_unpaired_by_the_shop_forgets_the_key(self):
        FakeShop.queue_answer = ShopError(401, "device-revoked")
        self.c.refresh_queue()
        self.assertIsNone(self.c.paired)
        self.assertFalse(os.path.exists(os.path.join(self.c.root, app.PAIRED_FILE)))
        self.assertEqual(self.c.view()["problem"], "unpaired")
        self.assertEqual(app.Controller(self.c.root, shop_factory=FakeShop).paired, None, "and after a restart")

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
