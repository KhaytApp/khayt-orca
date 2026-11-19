"""Khayt for OrcaSlicer, as OrcaSlicer loads it (needs Orca's `orca` module; 2.5 or later).

Two capabilities (Orca plugin docs: plugin_development.html, api_reference/pages.html):
- "Khayt: send to inbox", a slicing-pipeline step at G-code post-process: the
  exported file goes into the outbox, and the worker thread delivers it. It never
  blocks slicing on the network and never asks the Mac to print.
- "Khayt", a tab: pairing, the shop's queue (numbers, stages, machines — never
  customer data, which the Mac does not send), and what this plugin sent.

All the logic is `app.Controller`; this file only binds it to Orca.
"""

from __future__ import annotations

import os
import threading

import orca  # provided by OrcaSlicer

from . import app, capture, tab
from .compat import VERSION
from .paths import storage_root
from . import pairing
from .pairing import PairingError
from .warm import warm

# Everything the plugin's threads use, imported now, at load (see warm.py).
warm()

_controller = None


def controller() -> "app.Controller":
    global _controller
    if _controller is None:
        _controller = app.Controller(storage_root(), version=VERSION)
        _controller.start(visible=lambda: _Tab.visible)
    return _controller


# The status a refused export reports. OrcaSlicer v2.5.1 binds the enum in
# src/slic3r/plugin/PythonPluginBridge.cpp:387-391 (Success, Skipped,
# RecoverableError, FatalError); resolved once, here, so a renamed member can
# never raise inside the export handler.
_PluginResult = getattr(orca, "PluginResult", None)
FAILED = getattr(_PluginResult, "RecoverableError", None) or getattr(_PluginResult, "FatalError", None)


def _failed(message: str):
    """A failure result Orca accepts. If even that cannot be built (a status Orca does
    not know, a changed signature), report it as skipped: the export never sees an
    exception from this plugin."""
    try:
        if FAILED is None:
            raise TypeError("no PluginResult status")
        return orca.ExecutionResult.failure(FAILED, message)
    except Exception:  # noqa: BLE001
        return orca.ExecutionResult.skipped(message=message)


class _Capture(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Khayt: send to inbox"

    def execute(self, ctx):
        try:
            path = getattr(ctx, "gcode_path", None)
            if not path:
                return orca.ExecutionResult.skipped(message="no G-code")
            c = controller()
            if not c.paired:
                return orca.ExecutionResult.skipped(message="Not paired with a Khayt shop")

            def cfg(key):
                try:
                    return ctx.config_value(key)
                except Exception:  # noqa: BLE001 - a missing key is just not said
                    return None
            # `path` is Orca's working copy (".<pid>.<n>.gcode.pp"); the shop sees the
            # final name, `ctx.output_name` (capture.export_name).
            name = capture.export_name(path, getattr(ctx, "output_name", None))
            c.send(path, name=name, slicer={"name": "OrcaSlicer", "plugin": VERSION},
                   printer={"preset": cfg("printer_settings_id")},
                   process={"preset": cfg("print_settings_id")})
        except Exception as e:  # noqa: BLE001 - never raise into Orca's export: say why instead
            return _failed(f"Khayt: {e}")
        return orca.ExecutionResult.success(message="Queued for the Khayt inbox")


class _Tab(orca.pages.PagesPluginCapabilityBase):
    visible = False

    def get_name(self):
        return "Khayt"

    def get_ui(self):
        return tab.HTML

    def _push(self):
        self.post_message({"type": "view", "view": controller().view()})

    def on_message(self, data):
        c = controller()
        c.on_change = self._push
        kind = (data or {}).get("type")
        if kind == "ready":
            _Tab.visible = True
            self._push()
        elif kind == "hidden":
            _Tab.visible = False
        elif kind == "refresh":
            threading.Thread(target=c.refresh_queue, daemon=True).start()
        elif kind == "pair":
            base = str(data.get("address") or "").strip()
            if not base.startswith("http://"):
                base = "http://" + base
            # Here, on Orca's main thread, so Orca can ask about this host once
            # and remember it; the pairing then runs on a thread.
            try:
                pairing.reach(base)
            except PermissionError:
                self.post_message({"type": "pair-failed", "reason": "permission"})
                return
            except ValueError:
                self.post_message({"type": "pair-failed", "reason": "address"})
                return
            except OSError:
                self.post_message({"type": "pair-failed", "reason": "offline"})
                return

            def run():
                try:
                    c.pair(base, data.get("name") or "OrcaSlicer",
                           lambda check: self.post_message({"type": "check", "check": check}))
                    self.post_message({"type": "paired"})
                except PairingError as e:
                    self.post_message({"type": "pair-failed", "reason": e.reason})
                except OSError:
                    self.post_message({"type": "pair-failed", "reason": "offline"})
            threading.Thread(target=run, daemon=True).start()
        elif kind == "qty":
            # The page waits 600 ms after the last click before it says so.
            threading.Thread(target=c.ask_price, args=(data.get("qty"),), daemon=True).start()
        elif kind == "price-retry":
            threading.Thread(target=c.ask_price, daemon=True).start()
        elif kind == "unpair":
            threading.Thread(target=c.forget_pairing, daemon=True).start()


@orca.plugin
class KhaytPlugin(orca.base):
    def register_capabilities(self):
        # Asked once, at load: the worker thread. Files live in Orca's own data
        # folder (no question needed), and the shop's Mac is asked about once, by
        # host, the first time the plugin connects to it (http.client, never urllib).
        orca.request_permissions(threading=True)
        orca.register_capability(_Capture)
        orca.register_capability(_Tab)
