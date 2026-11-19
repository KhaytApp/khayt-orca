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
    # As strict as Orca's pybind11 bindings (PythonPluginBridge.cpp at v2.5.1): pybind
    # refuses an int where it wants the PluginResult enum, and a non-str message.
    # Nov 19 2026, third real run: failure(1, ...) passed the old lax stand-in and
    # raised TypeError inside Orca.
    class PluginResult:
        def __init__(self, name):
            self.name = name

        def __repr__(self):
            return "PluginResult." + self.name
    for n in ("Success", "Skipped", "RecoverableError", "FatalError"):
        setattr(PluginResult, n, PluginResult(n))
    o.PluginResult = PluginResult

    def strict(fn, **types_):
        def call(*args, **kw):
            names = list(types_)
            if len(args) > len(names) or set(kw) - set(names):
                raise TypeError(f"{fn}(): incompatible function arguments")
            given = dict(zip(names, args), **kw)
            for k, want in types_.items():
                if k in given and not isinstance(given[k], want):
                    raise TypeError(f"{fn}(): incompatible function arguments. Invoked with: {given[k]!r}")
            return given
        return call

    def _success(*a, **kw):
        g = strict("success", message=str, data=str)(*a, **kw)
        return Result("success", g.get("message", ""))

    def _skipped(*a, **kw):
        g = strict("skipped", message=str)(*a, **kw)
        return Result("skipped", g.get("message", ""))

    def _failure(*a, **kw):
        g = strict("failure", status=PluginResult, message=str, data=str)(*a, **kw)
        if "status" not in g or "message" not in g:
            raise TypeError("failure(): incompatible function arguments")
        r = Result("failure", g["message"])
        r.status = g["status"]
        return r

    o.ExecutionResult = types.SimpleNamespace(success=_success, skipped=_skipped, failure=_failure)
    o.slicing = types.SimpleNamespace(SlicingPipelineCapabilityBase=type("SlicingBase", (), {}))

    class PagesBase:
        def post_message(self, data):
            self.sent = getattr(self, "sent", []) + [data]
    o.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PagesBase)
    return o
