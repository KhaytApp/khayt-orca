"""The shop's price in the Khayt tab: every answer the Mac can give, against a strict stand-in.

Wire: Khayt's docs/LAN_API.md, `POST /api/connectors/price` (Mac #2595). The
stand-in refuses any request that is not exactly what the route takes, so a
plugin that sent a name, a file or an out-of-range quantity fails here first.
"""

import json
import os
import pathlib
import tempfile
import threading
import unittest

from khayt_orca import app, pairing, price
from khayt_orca.transport import ShopError

PAIRED = pairing.Paired(base="http://192.0.2.20:8765", device_id="dev_FakePluginDevice01",
                        key=pairing.b64(bytes(32)), mac_identity="")

# Sample figures only (never the shop's real book).
PRICE_ONLY = {"prices": "on", "currency": "SAR", "qty": 3, "unit": 41.5, "total": 124.5,
              "rounded": True, "basis": "assumed"}
WITH_COST = {**PRICE_ONLY, "cost": {"unit": 28.1, "total": 84.3}, "margin": {"pct": 32.29}}

GCODE = """G28
G1 X10 Y10
; filament used [g] = 272.40, 0.00
; filament_type = PLA;PETG
; filament_colour = #1E1E1E;#FFFFFF
; estimated printing time (normal mode) = 14h 54m 0s
"""

ALLOWED_FACT_KEYS = {"v", "qty", "timeSec", "layers", "toolchanges", "filaments", "printer", "process"}
ALLOWED_FILAMENT_KEYS = {"slot", "type", "colour", "grams"}


class StrictShop:
    """The Mac's connector routes, as strict as the route: wrong shapes raise AssertionError."""
    scopes = ["slices:send", "queue:read"]
    answers = []          # one per price call: a dict, or an exception to raise
    asked = []
    whoami_calls = 0
    hold = {}             # qty → threading.Event: the answer for that quantity waits for it
    answer_for = None     # facts → answer, instead of `answers`

    def __init__(self, base, paired):
        pass

    def whoami(self):
        StrictShop.whoami_calls += 1
        if isinstance(StrictShop.scopes, BaseException):
            raise StrictShop.scopes
        return {"connectorId": PAIRED.device_id, "scopes": list(StrictShop.scopes)}

    def price(self, facts):
        assert set(facts) <= ALLOWED_FACT_KEYS, set(facts) - ALLOWED_FACT_KEYS
        assert facts["v"] == 1
        assert isinstance(facts["qty"], int) and 1 <= facts["qty"] <= 999
        assert facts["timeSec"] > 0 and 1 <= len(facts["filaments"]) <= 16
        for f in facts["filaments"]:
            assert set(f) <= ALLOWED_FILAMENT_KEYS and f["grams"] > 0, f
        assert len(json.dumps(facts).encode()) <= 8 * 1024
        StrictShop.asked.append(facts)
        if facts["qty"] in StrictShop.hold:
            StrictShop.hold[facts["qty"]].wait(5)
        a = StrictShop.answer_for(facts) if StrictShop.answer_for else StrictShop.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return json.loads(json.dumps(a))

    # The rest of the routes, for the worker.
    def announce(self, capture):
        return {"sliceId": "S" * 16, "state": "awaiting-file"}

    def upload(self, slice_id, path, sha):
        return {"state": "in-inbox"}

    def queue(self, etag=None):
        return None, etag

    def unpair(self):
        pass


class PriceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        StrictShop.scopes = ["slices:send", "queue:read", "price:read"]
        StrictShop.answers, StrictShop.asked, StrictShop.whoami_calls = [], [], 0
        StrictShop.hold, StrictShop.answer_for = {}, None
        self.root = os.path.join(self.tmp.name, "store")
        self.c = app.Controller(self.root, shop_factory=StrictShop)
        self.c.paired = PAIRED
        self.c._save_paired(PAIRED)

    def tearDown(self):
        self.c.stop()
        self.tmp.cleanup()

    def export(self):
        p = os.path.join(self.tmp.name, "Benchy_PLA.gcode")
        pathlib.Path(p).write_text(GCODE, encoding="utf-8")
        self.c.send(p, printer={"preset": "Snapmaker U1 0.4 nozzle"}, process={"preset": "0.20mm Standard"})

    def ask(self, answer, qty=3):
        StrictShop.answers.append(answer)
        return self.c.ask_price(qty)

    # -- what is asked ---------------------------------------------------------------

    def test_the_request_is_the_facts_and_a_quantity_and_nothing_else(self):
        self.export()
        self.ask(PRICE_ONLY)
        sent = StrictShop.asked[0]
        self.assertEqual(sent["qty"], 3)
        self.assertEqual(sent["timeSec"], 53640)
        self.assertEqual(sent["filaments"], [{"slot": 1, "type": "PLA", "colour": "#1E1E1E", "grams": 272.4}],
                         "a slot that used nothing is not asked about")
        self.assertEqual(sent["printer"], {"preset": "Snapmaker U1 0.4 nozzle"})
        self.assertNotIn("Benchy", json.dumps(sent), "no file name goes with the price")

    def test_a_new_export_starts_at_quantity_one_and_drops_the_old_price(self):
        self.export()
        self.ask(PRICE_ONLY)
        self.assertEqual(self.c.view()["price"]["total"], 124.5)
        self.export()
        self.assertIsNone(self.c.view()["price"], "the last plate's price is not this plate's")
        self.assertEqual(self.c.qty, 1)
        self.assertTrue(self.c._price_wanted, "the worker asks, not Orca's export thread")

    def test_quantity_is_kept_between_1_and_999(self):
        self.export()
        StrictShop.answers.append({**PRICE_ONLY, "qty": 999})
        self.c.ask_price(5000)
        self.assertEqual(StrictShop.asked[-1]["qty"], 999)
        StrictShop.answers.append({**PRICE_ONLY, "qty": 1})
        self.c.ask_price(-3)
        self.assertEqual(StrictShop.asked[-1]["qty"], 1)

    def test_nothing_to_price_asks_nothing(self):
        p = os.path.join(self.tmp.name, "plate.bgcode")
        pathlib.Path(p).write_bytes(b"GCDE\x01\x00")
        self.c.send(p)
        self.assertIsNone(self.c.ask_price())
        self.assertEqual(StrictShop.asked, [], "a binary G-code has no figures to send")

    # -- no block ----------------------------------------------------------------------

    def test_without_price_read_there_is_no_block_and_no_ask(self):
        StrictShop.scopes = ["slices:send", "queue:read"]
        self.export()
        self.assertIsNone(self.c.ask_price(3))
        self.assertIsNone(self.c.view()["price"])
        self.assertEqual(StrictShop.asked, [])

    def test_off_is_no_block(self):
        self.export()
        self.assertIsNone(self.ask({"prices": "off"}))
        self.assertIsNone(self.c.view()["price"])

    def test_a_404_is_no_block_and_not_khayt_too_old(self):
        self.export()
        self.assertIsNone(self.ask(ShopError(404, "not-found")))
        self.assertIsNone(self.c.view()["price"])
        self.assertIsNone(self.c.view()["problem"], "the shop chose nothing; the queue says when Khayt is old")

    def test_answers_that_do_not_fit_are_no_block(self):
        self.export()
        for bad in ({"prices": "maybe"}, {"prices": "on"}, {**PRICE_ONLY, "currency": "sar"},
                    {**PRICE_ONLY, "total": "124.50"}, {**PRICE_ONLY, "unit": float("nan")},
                    {**PRICE_ONLY, "qty": True}, {"prices": "on", "unpriced": "something-new"}, []):
            self.assertIsNone(self.ask(bad), bad)
        self.assertIsNone(self.c.view()["price"])

    def test_an_answer_for_another_quantity_is_dropped(self):
        self.export()
        self.assertIsNone(self.ask({**PRICE_ONLY, "qty": 2}, qty=3))

    # -- the block ---------------------------------------------------------------------

    def test_price_only(self):
        self.export()
        self.ask(PRICE_ONLY)
        self.assertEqual(self.c.view()["price"], {"state": "on", "qty": 3, "currency": "SAR", "unit": 41.5,
                                                  "total": 124.5, "rounded": True, "basis": "assumed"})

    def test_cost_and_margin_only_with_cost_read(self):
        self.export()
        self.ask(WITH_COST)
        self.assertNotIn("cost", self.c.view()["price"], "a cost the device was not granted is not shown")
        self.assertNotIn("margin", self.c.view()["price"])
        StrictShop.scopes = ["slices:send", "queue:read", "price:read", "cost:read"]
        self.c.scopes = None
        self.ask(WITH_COST)
        got = self.c.view()["price"]
        self.assertEqual(got["cost"], {"unit": 28.1, "total": 84.3})
        self.assertEqual(got["margin"], {"pct": 32.29}, "no amount unless the Mac sends one: the plugin never subtracts")
        self.ask({**WITH_COST, "margin": {"pct": 32.29, "amount": 40.2}})
        self.assertEqual(self.c.view()["price"]["margin"]["amount"], 40.2)

    def test_cost_read_without_cost_in_the_answer_has_no_cost_keys(self):
        StrictShop.scopes = ["price:read", "cost:read"]
        self.export()
        self.ask(PRICE_ONLY)
        self.assertEqual(set(self.c.view()["price"]), {"state", "qty", "currency", "unit", "total", "rounded", "basis"})

    def test_unknown_keys_from_the_mac_are_not_passed_on(self):
        self.export()
        self.ask({**PRICE_ONLY, "customer": "Sample Customer", "note": "x"})
        self.assertNotIn("customer", json.dumps(self.c.view()))

    def test_unpriced_says_why_and_never_a_figure(self):
        self.export()
        self.ask({"prices": "on", "unpriced": "no-material"})
        self.assertEqual(self.c.view()["price"], {"state": "unpriced", "reason": "no-material", "qty": 3, "type": "PLA"})
        for why in ("no-currency", "bad-facts"):
            self.ask({"prices": "on", "unpriced": why})
            self.assertEqual(self.c.view()["price"], {"state": "unpriced", "reason": why, "qty": 3})

    # -- never stale -----------------------------------------------------------------------

    def test_unreachable_never_keeps_the_last_number(self):
        self.export()
        self.ask(PRICE_ONLY)
        for failure in (OSError("no route"), ShopError(429, "rate"), ShopError(503, "busy"),
                        ShopError(200, "seal"), ShopError(200, "unsigned-answer")):
            self.ask(PRICE_ONLY)
            self.assertEqual(self.c.view()["price"]["total"], 124.5)
            self.ask(failure)
            self.assertEqual(self.c.view()["price"], {"state": "unreachable", "qty": 3}, failure)

    def _wait_asked(self, qty):
        for _ in range(400):
            if any(f["qty"] == qty for f in StrictShop.asked):
                return
            threading.Event().wait(0.005)
        self.fail("never asked")

    def test_while_asking_no_figure_is_shown(self):
        self.export()
        self.ask(PRICE_ONLY)
        StrictShop.hold = {4: threading.Event()}
        StrictShop.answer_for = lambda f: {**PRICE_ONLY, "qty": f["qty"], "total": 166.0}
        t = threading.Thread(target=self.c.ask_price, args=(4,))
        t.start()
        self._wait_asked(4)
        self.assertEqual(self.c.view()["price"], {"state": "asking", "qty": 4}, "the qty-3 figure is gone")
        StrictShop.hold[4].set()
        t.join(5)
        self.assertEqual(self.c.view()["price"]["total"], 166.0)

    def test_an_older_answer_never_lands_after_a_newer_question(self):
        self.export()
        StrictShop.hold = {3: threading.Event()}
        StrictShop.answer_for = lambda f: {**PRICE_ONLY, "qty": f["qty"], "total": 41.5 * f["qty"]}
        slow = threading.Thread(target=self.c.ask_price, args=(3,))
        slow.start()
        self._wait_asked(3)
        self.c.ask_price(5)                                       # answered at once
        self.assertEqual(self.c.view()["price"]["qty"], 5)
        StrictShop.hold[3].set()                                  # now the qty-3 answer arrives
        slow.join(5)
        self.assertEqual(self.c.view()["price"]["qty"], 5)
        self.assertEqual(self.c.qty, 5)

    def test_losing_price_read_takes_the_block_away(self):
        self.export()
        self.ask(PRICE_ONLY)
        StrictShop.scopes = ["slices:send", "queue:read"]
        self.c.refresh_scopes()
        self.assertIsNone(self.c.view()["price"])

    def test_scopes_are_asked_at_most_once_a_minute(self):
        self.export()
        for _ in range(4):
            self.ask(PRICE_ONLY)
        self.assertEqual(StrictShop.whoami_calls, 1)

    def test_unpairing_forgets_the_plate_and_the_price(self):
        self.export()
        self.ask(PRICE_ONLY)
        self.c.forget_pairing()
        self.assertIsNone(self.c.view()["price"])
        self.assertIsNone(self.c._plate)

    # -- never on disk -----------------------------------------------------------------------

    def test_the_price_never_touches_disk(self):
        self.export()
        StrictShop.scopes = ["price:read", "cost:read"]
        self.c.scopes = None
        self.ask({**WITH_COST, "unit": 41.37, "total": 124.11, "cost": {"unit": 28.03, "total": 84.09},
                  "margin": {"pct": 32.24}})
        self.assertEqual(self.c.view()["price"]["total"], 124.11)
        for dirpath, _, files in os.walk(self.root):
            for name in files:
                text = pathlib.Path(dirpath, name).read_bytes()
                for figure in (b"124.11", b"41.37", b"84.09", b"28.03", b"32.24", b"prices"):
                    self.assertNotIn(figure, text, os.path.join(dirpath, name))


class Read(unittest.TestCase):
    def test_facts_skip_a_plate_with_no_time_or_no_weight(self):
        self.assertIsNone(price.facts({"filaments": [{"grams": 3}]}))
        self.assertIsNone(price.facts({"timeSec": 60, "filaments": [{"grams": 0}]}))
        self.assertIsNone(price.facts(None))
        self.assertEqual(price.facts({"timeSec": 60, "filaments": [{"grams": 3, "cost": 1.2, "preset": "x"}]}),
                         {"timeSec": 60, "filaments": [{"grams": 3}]}, "only the keys the route takes")

    def test_an_unknown_basis_reads_as_assumed(self):
        got = price.read({**PRICE_ONLY, "basis": "guessed"}, 3, ["price:read"])
        self.assertEqual(got["basis"], "assumed")


if __name__ == "__main__":
    unittest.main()
