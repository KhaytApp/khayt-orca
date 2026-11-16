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


def fake_orca():
    o = types.ModuleType("orca")
    o.registered, o.permissions = [], {}

    class base:
        pass

    class Result:
        def __init__(self, kind, message=""):
            self.kind, self.message = kind, message

    o.base = base
    o.plugin = lambda cls: cls
    o.request_permissions = lambda **kw: o.permissions.update(kw)
    o.register_capability = lambda cls: o.registered.append(cls)
    o.ExecutionResult = types.SimpleNamespace(
        success=lambda message="", data="": Result("success", message),
        skipped=lambda message="": Result("skipped", message),
        failure=lambda status, message, data="": Result("failure", message))
    o.slicing = types.SimpleNamespace(SlicingPipelineCapabilityBase=type("SlicingBase", (), {}))

    class PagesBase:
        def post_message(self, data):
            self.sent = getattr(self, "sent", []) + [data]
    o.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PagesBase)
    return o


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

    def tearDown(self):
        if self.plugin._controller:
            self.plugin._controller.stop()
        del sys.modules["orca"]
        for m in [m for m in sys.modules if m.startswith("khayt_orca")]:
            del sys.modules[m]
        if self.home is not None:
            os.environ["HOME"] = self.home
        self.tmp.cleanup()

    def test_registers_the_export_step_and_the_tab_and_asks_for_a_thread_and_its_folder(self):
        import orca
        self.plugin.KhaytPlugin().register_capabilities()
        self.assertEqual([c().get_name() for c in orca.registered], ["Khayt: send to inbox", "Khayt"])
        self.assertEqual(orca.permissions, {"threading": True,
                                            "fs_readwrite": [os.path.join(self.tmp.name, ".khayt-orca")]})

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
