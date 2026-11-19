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

from . import pairing, price, transport
from .outbox import Outbox

PAIRED_FILE = "pairing.json"     # never "secret"/"cert"/"conf": Orca's audit hook refuses those
STATE_FILE = "state.json"
QUEUE_EVERY_VISIBLE = 15
QUEUE_EVERY_HIDDEN = 60
SLICE_CHECK_EVERY = 60
# While a price is on screen: ask /self this often whether this device may still see it.
SCOPES_EVERY = 60
# What the shop did with a file once it was in the inbox. Settled ones are not asked about again.
SETTLED = ("left-inbox", "expired", "gone")


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
        self.queue_at: Optional[float] = None   # when the Mac last answered the queue (epoch s)
        # The price block. MEMORY ONLY: never written to disk, the outbox or a log.
        # `price` is what the tab draws (None: no block); `_plate` the last export's facts.
        self.scopes: Optional[list] = None      # from GET /api/connectors/self
        self.scopes_at = 0.0
        self.qty = 1
        self.price: Optional[dict] = None
        self._plate: Optional[dict] = None
        self._price_seq = 0                     # an answer to an older question is dropped
        self._price_wanted = False
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
        # open() by PATH (owner-only via the opener): Orca asks about an open of a
        # bare file descriptor, and refuses it outright on a background thread.
        with open(tmp, "w", encoding="utf-8",
                  opener=lambda p, flags: os.open(p, flags, 0o600)) as f:
            json.dump(p.to_json(), f)
        os.replace(tmp, path)

    def pair(self, base: str, name: str, show_check: Callable[[str], None]) -> None:
        """Runs the whole v2 pairing (blocking: call from a thread). Raises PairingError."""
        p = pairing.pair_on_lan(base, name, self.version, show_check)
        with self._lock:
            self.paired = p
            self._save_paired(p)
            self.queue, self.etag, self.problem = None, None, None
            self._forget_price()
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
            self._forget_price()
        self.on_change()

    def shop(self):
        if not self.paired:
            raise transport.ShopError(0, "not-paired")
        return self.shop_factory(self.paired.base, self.paired)

    # -- sending ----------------------------------------------------------------

    def send(self, gcode_path: str, name: Optional[str] = None, **capture_kw) -> dict:
        """After an export: queue the file (copied, under `name` if given) and wake the worker.
        Never blocks on the network."""
        item = self.outbox.add(gcode_path, name=name, **capture_kw)
        # A new plate: the old price is not this plate's. Quantity starts at 1 again,
        # and the worker asks the Mac (never this thread: it is Orca's export).
        with self._lock:
            self._plate = price.facts(item.get("capture"))
            self.qty, self.price = 1, None
            self._price_seq += 1
            self._price_wanted = self._plate is not None
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
            self.queue_at = self.clock()        # a 304 is an answer too: the queue is current
            self.problem = None
        except transport.ShopError as e:
            if e.reason in ("device-revoked", "device-unknown"):
                self.forget_pairing(tell_the_mac=False)
                self.problem = "unpaired"
            elif e.status == 404:
                # Paired, signed, and the queue route is not there: a Khayt for
                # Mac older than this plugin (compat.MIN_KHAYT_MAC), or one with
                # "Pair a slicer" switched off again.
                self.problem = "khayt-too-old"
            elif e.status != 429:
                self.problem = e.reason
        except OSError:
            self.problem = "offline"
        self.on_change()
        return self.queue

    def refresh_sent(self) -> int:
        """Ask the Mac what became of delivered files (at most once a minute each, until settled):
        still in the inbox, taken by the shop, or gone. Only the state comes back, never a job."""
        if not self.paired:
            return 0
        asked = 0
        now = self.clock()
        for item in self.outbox.items():
            if item["state"] != "done" or not item.get("sliceId") or item.get("macState") in SETTLED:
                continue
            if now - item.get("checkedAt", 0) < SLICE_CHECK_EVERY:
                continue
            try:
                state = self.shop().slice_state(item["sliceId"])
            except transport.ShopError as e:
                if e.status != 404:
                    continue
                state = "gone"
            except OSError:
                break
            self.outbox.mark(item["id"], macState=state, checkedAt=now)
            asked += 1
        if asked:
            self.on_change()
        return asked

    # -- the shop's price for the last plate ------------------------------------------

    def _forget_price(self) -> None:
        self._plate, self.price, self.scopes, self.qty = None, None, None, 1
        self._price_seq += 1
        self._price_wanted = False

    def refresh_scopes(self) -> Optional[list]:
        """What this device may do now (`price:read`, `cost:read`), from the Mac.
        Losing `price:read` takes the block away at once."""
        if not self.paired:
            return None
        try:
            got = self.shop().whoami().get("scopes")
        except (transport.ShopError, OSError):
            return self.scopes                  # unknown now: keep what was known
        self.scopes = [s for s in got if isinstance(s, str)] if isinstance(got, list) else []
        self.scopes_at = self.clock()
        if "price:read" not in self.scopes and self.price is not None:
            with self._lock:
                self.price = None
                self._price_seq += 1
            self.on_change()
        return self.scopes

    def ask_price(self, qty=None) -> Optional[dict]:
        """Ask the Mac for the last plate's price (blocking: a thread or the worker).

        No block unless /self holds `price:read` and the Mac answers "on". A failure
        never leaves a number on screen: the block says the Mac could not be reached."""
        with self._lock:
            if qty is not None:
                self.qty = price.clamp_qty(qty)
            self._price_wanted = False
            plate, q = self._plate, self.qty
            self._price_seq += 1
            seq = self._price_seq
        if not plate or not self.paired:
            self._set_price(seq, None)
            return None
        if self.scopes is None or self.clock() - self.scopes_at >= SCOPES_EVERY:
            self.refresh_scopes()
        scopes = list(self.scopes or ())
        if "price:read" not in scopes:
            self._set_price(seq, None)
            return None
        # The old figure is for another quantity or another moment: never shown meanwhile.
        self._set_price(seq, {"state": "asking", "qty": q})
        try:
            answer = self.shop().price(price.request(plate, q))
            block = price.read(answer, q, scopes, plate)
        except transport.ShopError as e:
            if e.status == 429 or e.status >= 500 or e.reason in ("unsigned-answer", "seal"):
                block = {"state": "unreachable", "qty": q}
            else:
                # 404: an older Mac, or pairing switched off. Not "khayt-too-old" here:
                # the queue says that when it is so. Any other refusal: no block.
                block = None
        except OSError:
            block = {"state": "unreachable", "qty": q}
        except ValueError:
            block = None                        # facts too large to ask about
        self._set_price(seq, block)
        return block

    def _set_price(self, seq: int, block: Optional[dict]) -> None:
        with self._lock:
            if seq != self._price_seq:
                return                          # a newer question is out
            self.price = block
        self.on_change()

    # -- the worker ---------------------------------------------------------------

    def start(self, visible: Callable[[], bool] = lambda: True) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()

        def run():
            last_queue = 0.0
            while not self._stop.is_set():
                if self._price_wanted:
                    self.ask_price()
                elif self.price is not None and self.clock() - self.scopes_at >= SCOPES_EVERY:
                    self.refresh_scopes()
                self.deliver()
                every = QUEUE_EVERY_VISIBLE if visible() else QUEUE_EVERY_HIDDEN
                if self.clock() - last_queue >= every:
                    self.refresh_queue()
                    self.refresh_sent()
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
        sent = [{"file": i["file"], "state": i["state"], "reason": i.get("reason"), "shop": i.get("macState")}
                for i in self.outbox.items()[-20:]]
        return {
            "paired": bool(self.paired),
            "shop": (q.get("shop") or {}).get("name") if isinstance(q.get("shop"), dict) else None,
            "jobs": jobs, "more": q.get("more"), "machines": machines,
            "inboxWaiting": (q.get("inbox") or {}).get("waiting") if isinstance(q.get("inbox"), dict) else None,
            "sent": sent, "problem": self.problem,
            # The tab says when the queue was last current, and that it is stale
            # when a refresh fails (the last good queue stays on screen).
            "updatedAt": int(self.queue_at * 1000) if self.queue_at else None,
            # The price block (price.read allowlisted it), or None: no block at all.
            "price": dict(self.price) if self.price else None,
        }
