"""The plugin's state, without OrcaSlicer: pairing, the outbox and its worker, the tab's view.

`plugin.py` is the thin part that only runs inside Orca (`import orca`); everything
it does goes through a `Controller`, which the tests drive directly.

What the tab may show is what the Mac sends (`lib/connector-queue.js`: job number,
stage, machine, the file THIS plugin sent; machines; how many files wait) plus this
plugin's own outbox. There is no customer data to show: the Mac never sends any.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Callable, Optional

from . import pairing, transport
from .outbox import Outbox

PAIRED_FILE = "pairing.json"     # never "secret"/"cert"/"conf": Orca's audit hook refuses those
STATE_FILE = "state.json"
QUEUE_EVERY_VISIBLE = 15
QUEUE_EVERY_HIDDEN = 60


class Controller:
    def __init__(self, root: str, *, version: str = "0.1.0", slicer_version: str = "",
                 shop_factory: Callable[[str, "pairing.Paired"], object] = transport.Shop,
                 clock: Callable[[], float] = time.time):
        self.root = root
        self.version = version
        self.slicer_version = slicer_version
        self.shop_factory = shop_factory
        self.clock = clock
        os.makedirs(root, exist_ok=True)
        self.outbox = Outbox(os.path.join(root, "outbox"), clock=clock)
        self.paired: Optional[pairing.Paired] = self._load_paired()
        self.queue: Optional[dict] = None
        self.etag: Optional[str] = None
        self.problem: Optional[str] = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self.on_change: Callable[[], None] = lambda: None

    # -- pairing ----------------------------------------------------------------

    def _load_paired(self) -> Optional[pairing.Paired]:
        try:
            with open(os.path.join(self.root, PAIRED_FILE), encoding="utf-8") as f:
                return pairing.Paired.from_json(json.load(f))
        except (OSError, ValueError, KeyError):
            return None

    def _save_paired(self, p: Optional[pairing.Paired]) -> None:
        path = os.path.join(self.root, PAIRED_FILE)
        if p is None:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(p.to_json(), f)
        os.replace(tmp, path)

    def pair(self, base: str, name: str, show_check: Callable[[str], None]) -> None:
        """Runs the whole v2 pairing (blocking: call from a thread). Raises PairingError."""
        p = pairing.pair_on_lan(base, name, self.version, show_check)
        with self._lock:
            self.paired = p
            self._save_paired(p)
            self.queue, self.etag, self.problem = None, None, None
        self._wake.set()
        self.on_change()

    def forget_pairing(self, tell_the_mac: bool = True) -> None:
        if tell_the_mac and self.paired:
            try:
                self.shop().unpair()
            except (transport.ShopError, OSError):
                pass  # the Mac may already have removed it; forgetting here is what matters
        with self._lock:
            self.paired = None
            self._save_paired(None)
            self.queue, self.etag = None, None
        self.on_change()

    def shop(self):
        if not self.paired:
            raise transport.ShopError(0, "not-paired")
        return self.shop_factory(self.paired.base, self.paired)

    # -- sending ----------------------------------------------------------------

    def send(self, gcode_path: str, **capture_kw) -> dict:
        """After an export: queue the file (copied) and wake the worker. Never blocks on the network."""
        item = self.outbox.add(gcode_path, **capture_kw)
        self._wake.set()
        self.on_change()
        return item

    def deliver(self) -> list:
        """One round of the outbox (what the worker runs)."""
        if not self.paired:
            return []
        try:
            moved = self.outbox.drain(self.shop())
        except OSError:
            moved = []
        if moved:
            self.on_change()
        return moved

    def refresh_queue(self) -> Optional[dict]:
        """Ask for the queue (ETag: an unchanged queue costs the Mac nothing)."""
        if not self.paired:
            return None
        try:
            snap, etag = self.shop().queue(self.etag)
            if snap is not None:
                self.queue, self.etag = snap, etag
            self.problem = None
        except transport.ShopError as e:
            if e.reason in ("device-revoked", "device-unknown"):
                self.forget_pairing(tell_the_mac=False)
                self.problem = "unpaired"
            elif e.status != 429:
                self.problem = e.reason
        except OSError:
            self.problem = "offline"
        self.on_change()
        return self.queue

    # -- the worker ---------------------------------------------------------------

    def start(self, visible: Callable[[], bool] = lambda: True) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()

        def run():
            last_queue = 0.0
            while not self._stop.is_set():
                self.deliver()
                every = QUEUE_EVERY_VISIBLE if visible() else QUEUE_EVERY_HIDDEN
                if self.clock() - last_queue >= every:
                    self.refresh_queue()
                    last_queue = self.clock()
                self._wake.wait(timeout=5)
                self._wake.clear()

        self._worker = threading.Thread(target=run, name="khayt-outbox", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # -- what the tab shows -------------------------------------------------------

    def view(self) -> dict:
        """Everything the tab draws, as plain data. Only allowlisted keys pass."""
        q = self.queue or {}
        jobs = [{k: j.get(k) for k in ("number", "stage", "machine", "file")} for j in q.get("jobs", [])
                if isinstance(j, dict)]
        machines = [{k: m.get(k) for k in ("name", "state")} for m in q.get("machines", []) if isinstance(m, dict)]
        sent = [{"file": i["file"], "state": i["state"], "reason": i.get("reason")}
                for i in self.outbox.items()[-20:]]
        return {
            "paired": bool(self.paired),
            "shop": (q.get("shop") or {}).get("name") if isinstance(q.get("shop"), dict) else None,
            "jobs": jobs, "more": q.get("more"), "machines": machines,
            "inboxWaiting": (q.get("inbox") or {}).get("waiting") if isinstance(q.get("inbox"), dict) else None,
            "sent": sent, "problem": self.problem,
        }
