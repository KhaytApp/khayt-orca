"""plugin.py against a stand-in `orca` module: what it registers, and what an export does.

The real module exists only inside OrcaSlicer; this stand-in has just the names the
docs give (plugin_development.html, api_reference/pages.html, registry.html)."""

import importlib
import os
import pathlib
import sys
import tempfile
import types
import unittest


from tests.fake_orca_module import fake_orca  # noqa: E402


class Entry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.environ.get("HOME")
        os.environ["HOME"] = self.tmp.name
        os.environ["USERPROFILE"] = self.tmp.name
        sys.modules["orca"] = fake_orca()
        for m in [m for m in sys.modules if m.startswith("khayt_orca")]:
            del sys.modules[m]
        self.plugin = importlib.import_module("khayt_orca.plugin")
        # Not installed under an orca_plugins folder here: stand in for Orca's data folder.
        self.plugin.storage_root = lambda: os.path.join(self.tmp.name, "OrcaSlicer", "khayt-orca")

    def tearDown(self):
        if self.plugin._controller:
            self.plugin._controller.stop()
        del sys.modules["orca"]
        for m in [m for m in sys.modules if m.startswith("khayt_orca")]:
            del sys.modules[m]
        if self.home is not None:
            os.environ["HOME"] = self.home
        self.tmp.cleanup()

    def test_registers_the_export_step_and_the_tab_and_asks_only_for_a_thread(self):
        import orca
        self.plugin.KhaytPlugin().register_capabilities()
        self.assertEqual([c().get_name() for c in orca.registered], ["Khayt: send to inbox", "Khayt"])
        # Its files are inside Orca's data folder, which needs no question.
        self.assertEqual(orca.permissions, {"threading": True})

    def test_an_export_before_pairing_is_skipped_not_failed(self):
        p = os.path.join(self.tmp.name, "a.gcode")
        pathlib.Path(p).write_text("G28\n", encoding="utf-8")
        ctx = types.SimpleNamespace(gcode_path=p, config_value=lambda k: None)
        r = self.plugin._Capture().execute(ctx)
        self.assertEqual(r.kind, "skipped")

    def test_the_tab_answers_ready_with_the_view(self):
        t = self.plugin._Tab()
        t.on_message({"type": "ready"})
        self.assertEqual(t.sent[-1]["type"], "view")
        self.assertFalse(t.sent[-1]["view"]["paired"])


class OrcaExport(Entry):
    """Nov 19 2026, third real run: Orca hands the post-process step a working copy named
    `.70276.0.gcode.pp` and the final name as ctx.output_name; the plugin refused the file,
    then raised TypeError on failure(1, ...) (pybind wants orca.PluginResult)."""

    def _paired(self):
        from khayt_orca import app
        from tests.test_app import PAIRED, FakeShop
        FakeShop.calls = []
        c = app.Controller(os.path.join(self.tmp.name, "store"), shop_factory=FakeShop)
        c.paired = PAIRED
        self.plugin._controller = c
        return c

    def _ctx(self, temp_name, output_name, body="G28\n"):
        d = os.path.join(self.tmp.name, "orca-tmp")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, temp_name)
        pathlib.Path(p).write_text(body, encoding="utf-8")
        return types.SimpleNamespace(gcode_path=p, output_name=output_name, host="File",
                                     config_value=lambda k: None)

    def test_the_working_copy_is_sent_under_its_final_name(self):
        c = self._paired()
        r = self.plugin._Capture().execute(self._ctx(".70276.0.gcode.pp", "Benchy_PLA_0.2mm.gcode"))
        self.assertEqual(r.kind, "success", r.message)
        [item] = c.outbox.items()
        self.assertEqual(item["file"], "Benchy_PLA_0.2mm.gcode")
        self.assertEqual(item["capture"]["file"]["name"], "Benchy_PLA_0.2mm.gcode")
        self.assertEqual(item["capture"]["file"]["type"], "gcode")

    def test_without_a_final_name_the_temp_prefix_and_pp_are_dropped(self):
        c = self._paired()
        r = self.plugin._Capture().execute(self._ctx(".70276.0.gcode.pp", ""))
        self.assertEqual(r.kind, "success", r.message)
        self.assertEqual(c.outbox.items()[0]["file"], "OrcaSlicer export.gcode")

    def test_a_refused_file_is_a_failure_with_an_orca_status_not_an_exception(self):
        import orca
        c = self._paired()
        r = self.plugin._Capture().execute(self._ctx(".70276.0.stl.pp", "part.stl"))
        self.assertEqual(r.kind, "failure")
        self.assertIs(r.status, orca.PluginResult.RecoverableError)
        self.assertIn("Khayt takes G-code", r.message)
        self.assertEqual(c.outbox.items(), [])
        self.assertEqual(os.listdir(c.outbox.root) if os.path.isdir(c.outbox.root) else [], [],
                         "no half-made outbox item left behind")

    def test_quantity_and_retry_ask_the_mac_on_a_thread(self):
        import threading
        c = self._paired()
        asked, done = [], threading.Event()

        def ask(qty=None):
            asked.append((qty, threading.current_thread() is threading.main_thread()))
            if len(asked) == 2:
                done.set()
        c.ask_price = ask
        t = self.plugin._Tab()
        t.on_message({"type": "qty", "qty": 7})
        t.on_message({"type": "price-retry"})
        self.assertTrue(done.wait(5))
        self.assertEqual(sorted(asked, key=str), sorted([(7, False), (None, False)], key=str),
                         "never on Orca's main thread")

    def test_the_stand_in_is_as_strict_as_pybind(self):
        import orca
        with self.assertRaises(TypeError):
            orca.ExecutionResult.failure(1, "x")
        with self.assertRaises(TypeError):
            orca.ExecutionResult.success(message=3)
        orca.ExecutionResult.failure(orca.PluginResult.FatalError, "x")


class ExportName(unittest.TestCase):
    def test_names(self):
        from khayt_orca.capture import export_name as n
        self.assertEqual(n("/t/.70276.0.gcode.pp", "Benchy.gcode"), "Benchy.gcode")
        self.assertEqual(n("/t/.70276.0.gcode.pp", "C:\\Users\\x\\Benchy.gcode"), "Benchy.gcode")
        self.assertEqual(n("/t/.70276.0.bgcode.pp", ""), "OrcaSlicer export.bgcode")
        self.assertEqual(n("/t/.70276.0.gcode.pp", None), "OrcaSlicer export.gcode")
        self.assertEqual(n("/t/Benchy.gcode", None), "Benchy.gcode")
        # A final name Khayt can't take falls back to the file itself, and capture then refuses it.
        self.assertEqual(n("/t/.1.0.stl.pp", "part.stl"), ".1.0.stl")


class FailureNeverRaises(OrcaExport):
    def test_failure_status_is_resolved_from_orcas_enum(self):
        import orca
        self.assertIs(self.plugin.FAILED, orca.PluginResult.RecoverableError)

    def test_if_failure_itself_throws_the_export_gets_skipped_with_the_reason(self):
        import orca

        def broken(*a, **kw):
            raise TypeError("failure(): incompatible function arguments")
        orca.ExecutionResult.failure = broken
        self._paired()
        r = self.plugin._Capture().execute(self._ctx(".70276.0.stl.pp", "part.stl"))
        self.assertEqual(r.kind, "skipped")
        self.assertIn("Khayt takes G-code", r.message)

    def test_without_a_known_status_it_still_answers(self):
        self.plugin.FAILED = None
        self._paired()
        r = self.plugin._Capture().execute(self._ctx(".70276.0.stl.pp", "part.stl"))
        self.assertEqual(r.kind, "skipped")

    def test_falls_back_to_fatal_error_when_recoverable_is_missing(self):
        import orca
        del orca.PluginResult.RecoverableError
        for m in [m for m in sys.modules if m.startswith("khayt_orca")]:
            del sys.modules[m]
        plugin = importlib.import_module("khayt_orca.plugin")
        self.assertIs(plugin.FAILED, orca.PluginResult.FatalError)
