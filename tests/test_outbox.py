"""The outbox delivers once, waits when the Mac says wait, and never loses an export."""

import os
import pathlib
import tempfile
import unittest

from khayt_orca.outbox import Outbox
from khayt_orca.transport import ShopError


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class FakeShop:
    """Answers from a script: each call pops the next reply (or raises it)."""

    def __init__(self, announce=(), upload=()):
        self.announces, self.uploads = list(announce), list(upload)
        self.calls = []

    def _next(self, q, what):
        self.calls.append(what)
        r = q.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r

    def announce(self, capture):
        return self._next(self.announces, "announce")

    def upload(self, slice_id, path, sha256):
        assert os.path.exists(path)
        return self._next(self.uploads, "upload " + slice_id)


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = Clock()
        self.box = Outbox(os.path.join(self.tmp.name, "outbox"), clock=self.clock)
        self.export = os.path.join(self.tmp.name, "Bracket_PLA.gcode")
        pathlib.Path(self.export).write_text("; total layers count = 3\nG28\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_announce_then_upload_then_done_and_the_export_can_go(self):
        item = self.box.add(self.export, intent="quote")
        os.remove(self.export)  # Orca cleans up its export; the outbox kept a copy
        shop = FakeShop(announce=[{"sliceId": "S" * 16, "state": "awaiting-file"}],
                        upload=[{"sliceId": "S" * 16, "state": "in-inbox"}])
        moved = self.box.drain(shop)
        self.assertEqual([m["state"] for m in moved], ["announced", "done"])
        self.assertEqual(shop.calls, ["announce", "upload " + "S" * 16])
        self.assertEqual(self.box.items()[0]["capture"]["layers"], 3)
        self.assertIsNone(self.box.step(shop), "nothing more is due")
        self.assertEqual(item["capture"]["intent"], "quote")

    def test_already_in_the_inbox_is_done_without_an_upload(self):
        self.box.add(self.export)
        shop = FakeShop(announce=[{"sliceId": "S" * 16, "state": "in-inbox", "duplicate": True}])
        self.assertEqual(self.box.drain(shop)[0]["state"], "done")
        self.assertEqual(shop.calls, ["announce"])

    def test_busy_and_rate_limited_wait_then_deliver(self):
        self.box.add(self.export)
        shop = FakeShop(announce=[ShopError(429, "rate", retry_after=120), {"sliceId": "S" * 16}],
                        upload=[ShopError(503, "busy"), {"state": "in-inbox"}])
        self.assertEqual(self.box.drain(shop)[0]["reason"], "rate")
        self.assertIsNone(self.box.step(shop), "waits the Retry-After")
        self.clock.t += 121
        self.assertEqual(self.box.step(shop)["state"], "announced")
        self.assertEqual(self.box.step(shop)["reason"], "busy")
        self.clock.t += 10
        self.assertEqual(self.box.step(shop)["state"], "done")

    def test_an_expired_slice_is_announced_again(self):
        self.box.add(self.export)
        shop = FakeShop(announce=[{"sliceId": "A" * 16}, {"sliceId": "B" * 16}],
                        upload=[ShopError(404, "unknown-slice"), {"state": "in-inbox"}])
        self.box.drain(shop)
        self.assertEqual(shop.calls, ["announce", "upload " + "A" * 16, "announce", "upload " + "B" * 16])
        self.assertEqual(self.box.items()[0]["state"], "done")

    def test_hash_mismatch_retried_once_then_failed(self):
        self.box.add(self.export)
        shop = FakeShop(announce=[{"sliceId": "S" * 16}],
                        upload=[ShopError(422, "hash-mismatch"), ShopError(422, "hash-mismatch")])
        self.box.drain(shop)
        item = self.box.items()[0]
        self.assertEqual((item["state"], item["reason"]), ("failed", "hash-mismatch"))

    def test_a_refusal_it_cannot_fix_fails_with_the_reason(self):
        self.box.add(self.export)
        shop = FakeShop(announce=[ShopError(422, "wrong-kind")])
        self.box.drain(shop)
        self.assertEqual(self.box.items()[0]["reason"], "wrong-kind")
        self.assertEqual(self.box.items()[0]["state"], "failed")

    def test_offline_backs_off_and_survives_a_restart(self):
        self.box.add(self.export)
        shop = FakeShop(announce=[ConnectionRefusedError()])
        self.box.drain(shop)
        again = Outbox(self.box.root, clock=self.clock)  # Orca restarted
        item = again.items()[0]
        self.assertEqual((item["state"], item["reason"]), ("new", "offline"))
        self.assertIsNone(again.due())
        self.clock.t += 10
        self.assertIsNotNone(again.due())

    def test_storage_names_avoid_what_orcas_audit_hook_refuses(self):
        self.box.add(self.export)
        for dirpath, dirs, files in os.walk(self.box.root):
            for name in dirs + files:
                for word in ("secret", "cert", "conf"):
                    self.assertNotIn(word, name.lower(), name)
