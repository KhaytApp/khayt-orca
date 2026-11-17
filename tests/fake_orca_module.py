"""A stand-in for OrcaSlicer's `orca` module (stdlib only, so importing it hides
nothing from tests/orca_audit_run.py)."""

import types


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
