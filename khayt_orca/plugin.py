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

from . import app, tab
from .compat import VERSION
from .paths import storage_root
from .pairing import PairingError
_controller = None


def controller() -> "app.Controller":
    global _controller
    if _controller is None:
        _controller = app.Controller(storage_root(), version=VERSION)
        _controller.start(visible=lambda: _Tab.visible)
    return _controller


class _Capture(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "Khayt: send to inbox"

    def execute(self, ctx):
        path = getattr(ctx, "gcode_path", None)
        c = controller()
        if not path:
            return orca.ExecutionResult.skipped(message="no G-code")
        if not c.paired:
            return orca.ExecutionResult.skipped(message="Not paired with a Khayt shop")
        try:
            def cfg(key):
                try:
                    return ctx.config_value(key)
                except Exception:  # noqa: BLE001 - a missing key is just not said
                    return None
            c.send(path, slicer={"name": "OrcaSlicer", "plugin": VERSION},
                   printer={"preset": cfg("printer_settings_id")},
                   process={"preset": cfg("print_settings_id")})
        except (OSError, ValueError) as e:
            return orca.ExecutionResult.failure(1, f"Khayt: {e}")
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
