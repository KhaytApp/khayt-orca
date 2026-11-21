"""plugin.py against a stand-in `orca` module: what it registers, and what an export does.

The real module exists only inside OrcaSlicer; this stand-in has just the names the
docs give (plugin_development.html, api_reference/pages.html, registry.html)."""

import importlib
import os
import pathlib
import sys
import tempfile
import unittest


from tests.fake_orca_module import deliver, fake_orca  # noqa: E402


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
        import orca
        ctx = orca.slicing.SlicingPipelineContext(gcode_path=p)
        r = self.plugin._Capture().execute(ctx)
        self.assertEqual(r.kind, "skipped")

    def test_the_tab_answers_ready_with_the_view(self):
        t = self.plugin._Tab()
        deliver(t, {"type": "ready"})
        self.assertEqual(t.sent[-1]["type"], "view")
        self.assertFalse(t.sent[-1]["view"]["paired"])

    def test_the_page_gets_the_view_exactly_as_the_controller_made_it(self):
        # post_message goes through JSON (PluginJsonPy.hpp): a tuple, a set or an
        # object would arrive as something else, and the page would draw that.
        t = self.plugin._Tab()
        deliver(t, {"type": "ready"})
        self.assertEqual(t.sent[-1], {"type": "view", "view": self.plugin.controller().view()})

    def test_a_message_that_is_not_an_object_is_ignored(self):
        # Orca hands on_message the raw string when the page's message is not JSON,
        # and any JSON value when it is (PagesPluginCapabilityTrampoline.hpp).
        t = self.plugin._Tab()
        for m in ("not json", "[1, 2]", "3", "null", '"ready"', '{"type": 3}', '{"type": ["ready"]}'):
            deliver(t, m)
        self.assertEqual(getattr(t, "sent", []), [])


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
        import orca
        return orca.slicing.SlicingPipelineContext(gcode_path=p, output_name=output_name, host="File")

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

    def test_the_stand_in_is_as_strict_as_pybind(self):
        import orca
        with self.assertRaises(TypeError):
            orca.ExecutionResult.failure(1, "x")
        with self.assertRaises(TypeError):
            orca.ExecutionResult.success(message=3)
        orca.ExecutionResult.failure(orca.PluginResult.FatalError, "x")
        # PythonPluginBridge.cpp
        with self.assertRaises(ValueError):
            orca.register_capability(type("Loose", (), {}))
        with self.assertRaises(ValueError):
            orca.plugin(type("Loose", (), {}))
        with self.assertRaises(TypeError):
            orca.request_permissions(threading=1)
        with self.assertRaises(TypeError):
            orca.request_permissions(fs_readwrite="/a")
        with self.assertRaises(ValueError):
            orca.request_permissions(fs_readwrite=[""])
        with self.assertRaises(TypeError):
            orca.request_permissions(files=["/a"])
        # SlicingPipelinePluginCapability.cpp
        ctx = orca.slicing.SlicingPipelineContext(gcode_path="/t/a.gcode", config={"k": "v"})
        with self.assertRaises(AttributeError):
            ctx.gcode_path = "/t/b.gcode"
        with self.assertRaises(TypeError):
            ctx.config_value(3)
        self.assertEqual((ctx.config_value("k"), ctx.config_value("nope")), ("v", None))
        # PluginJsonPy.hpp
        page = orca.pages.PagesPluginCapabilityBase()
        with self.assertRaises(OverflowError):
            page.post_message({"n": 2 ** 64})
        with self.assertRaises(UnicodeEncodeError):
            page.post_message({"s": "\ud800"})
        page.post_message({1: (1, 2), "x": {3}, "f": float("nan")})
        self.assertEqual(page.sent[-1], {"1": [1, 2], "x": "{3}", "f": None})

    def test_the_presets_are_read_from_orcas_config(self):
        import orca
        c = self._paired()
        d = os.path.join(self.tmp.name, "orca-tmp")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, ".1.0.gcode.pp")
        pathlib.Path(p).write_text("G28\n", encoding="utf-8")
        ctx = orca.slicing.SlicingPipelineContext(
            gcode_path=p, output_name="Cube.gcode", host="File",
            config={"printer_settings_id": "Sample printer 0.4", "print_settings_id": "0.20mm Standard"})
        self.assertEqual(self.plugin._Capture().execute(ctx).kind, "success")
        cap = c.outbox.items()[0]["capture"]
        self.assertEqual((cap["printer"]["preset"], cap["process"]["preset"]),
                         ("Sample printer 0.4", "0.20mm Standard"))


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
