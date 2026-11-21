"""A stand-in for OrcaSlicer's `orca` module (stdlib only, so importing it hides
nothing from tests/orca_audit_run.py).

It is as strict as Orca's pybind11 bindings at tag v2.5.1, read from the source,
because a lax stand-in passes code that then breaks inside Orca (Nov 19 2026,
third real run: failure(1, ...) passed the old one and raised TypeError there):

- src/slic3r/plugin/PythonPluginBridge.cpp: `register_capability` and
  `@orca.plugin` refuse a class that is not a subclass of Orca's own bases;
  `request_permissions` takes only its seven keywords, lists of non-empty
  strings and a bool; `ExecutionResult.failure` wants the PluginResult enum.
- pluginTypes/pages/PagesPluginCapability.cpp + PluginJsonPy.hpp:
  `post_message(data)` turns `data` into JSON and the page gets that JSON. A
  tuple becomes a list, a dict key becomes its str(), anything unknown becomes
  its str(), an int must fit in 64 bits and a str must be valid UTF-8.
- PagesPluginCapabilityTrampoline.hpp: `on_message` gets the page's message
  parsed as JSON, or the raw string when it is not JSON. `deliver()` does that.
- pluginTypes/slicingPipeline/SlicingPipelinePluginCapability.cpp:
  `SlicingPipelineContext` is read-only, and `config_value(key)` takes a str.
"""

import json
import math
import types

_INT64 = (-(2 ** 63), 2 ** 63 - 1)
_PERMISSION_LISTS = ("fs_read", "fs_readwrite", "network_http", "network_socket", "process", "network")


def _to_json(o, depth=0):
    """py_to_json (PluginJsonPy.hpp), in the same order of checks."""
    if depth > 200:
        raise ValueError("Plugin value nested too deeply (possible cycle)")
    if o is None:
        return None
    if isinstance(o, bool):
        return o
    if isinstance(o, int):
        if not _INT64[0] <= o <= _INT64[1]:
            raise OverflowError("int does not fit in std::int64_t")
        return o
    if isinstance(o, float):
        # nlohmann writes NaN and infinity as null.
        return o if math.isfinite(o) else None
    if isinstance(o, str):
        o.encode("utf-8")  # a lone surrogate cannot become std::string
        return o
    if isinstance(o, bytes):
        return o.decode("utf-8")  # nlohmann's dump() throws on invalid UTF-8
    if isinstance(o, dict):
        return {str(k): _to_json(v, depth + 1) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_to_json(e, depth + 1) for e in o]
    return str(o)


def deliver(tab, message):
    """What Orca does with a message the page posts: JSON text in, parsed value out
    (the raw string when it does not parse), then `tab.on_message(value)`."""
    text = message if isinstance(message, str) else json.dumps(message)
    try:
        value = json.loads(text)
    except ValueError:
        value = text
    return tab.on_message(value)


def fake_orca():
    o = types.ModuleType("orca")
    o.registered, o.permissions = [], {}

    class PythonPluginBase:
        """orca.PythonPluginBase: every capability base derives from it."""

    class base:
        """orca.base: the package class @orca.plugin marks."""

    class Result:
        def __init__(self, kind, message=""):
            self.kind, self.message = kind, message

    def _plugin(cls):
        if not isinstance(cls, type):
            raise ValueError("@orca.plugin must decorate a class")
        if not issubclass(cls, base):
            raise ValueError("@orca.plugin class must inherit from orca.base")
        return cls

    def _register_capability(cls):
        if not (isinstance(cls, type) and issubclass(cls, PythonPluginBase)):
            raise ValueError("Registered class must inherit from a PluginCapability base")
        o.registered.append(cls)

    def _request_permissions(*args, **kw):
        if args:
            raise TypeError("request_permissions(): incompatible function arguments")
        for k, v in kw.items():
            if k == "threading":
                if not isinstance(v, bool):
                    raise TypeError("request_permissions(): incompatible function arguments")
            elif k in _PERMISSION_LISTS:
                if isinstance(v, str) or not isinstance(v, (list, tuple)) or not all(isinstance(t, str) for t in v):
                    raise TypeError("request_permissions(): incompatible function arguments")
                if any(t == "" for t in v):
                    raise ValueError(f"request_permissions({k}=...) does not accept empty values")
            else:
                raise TypeError("request_permissions(): incompatible function arguments")
        o.permissions.update(kw)

    o.PythonPluginBase = PythonPluginBase
    o.base = base
    o.plugin = _plugin
    o.request_permissions = _request_permissions
    o.register_capability = _register_capability

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

    class Step:
        def __init__(self, name):
            self.name = name
    for n in ("posSlice", "psGCodePostProcess"):
        setattr(Step, n, Step(n))

    class SlicingPipelineContext:
        """Read-only like def_readonly; config_value(key: str) like the binding."""
        __slots__ = ("_v", "_config")

        def __init__(self, gcode_path="", output_name="", host="", config=None,
                     step=Step.psGCodePostProcess, orca_version="2.5.1"):
            object.__setattr__(self, "_v", {"gcode_path": gcode_path, "output_name": output_name,
                                            "host": host, "step": step, "orca_version": orca_version,
                                            "print": None, "object": None})
            object.__setattr__(self, "_config", dict(config or {}))

        def __getattr__(self, name):
            v = object.__getattribute__(self, "_v")
            if name in v:
                return v[name]
            raise AttributeError(f"'SlicingPipelineContext' object has no attribute '{name}'")

        def __setattr__(self, name, value):
            raise AttributeError(f"property of 'SlicingPipelineContext' object has no setter: {name}")

        def config_value(self, key):
            if not isinstance(key, str):
                raise TypeError("config_value(): incompatible function arguments")
            return object.__getattribute__(self, "_config").get(key)

        def cancelled(self):
            return False

    class SlicingBase(PythonPluginBase):
        pass
    o.slicing = types.SimpleNamespace(SlicingPipelineCapabilityBase=SlicingBase, Step=Step,
                                      SlicingPipelineContext=SlicingPipelineContext)

    class PagesBase(PythonPluginBase):
        def post_message(self, data):
            # What the page receives: the JSON Orca made of `data`.
            self.sent = getattr(self, "sent", []) + [json.loads(json.dumps(_to_json(data)))]
    o.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PagesBase)
    return o
