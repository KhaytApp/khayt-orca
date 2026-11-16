"""A send queue that survives OrcaSlicer quitting: export, then deliver when the Mac can take it.

Each item is a folder under the plugin's storage: `item.json` and a copy of the
file (Orca may delete its own export). `step()` moves one item one step on:

    new ──announce──► announced ──upload──► done
      ▲                   │
      └── slice expired ──┘   (the Mac forgot it: announce again)

The Mac answers `duplicate` when the same bytes are already in its inbox: that
is done too. A busy Mac (503), a rate limit (429) or no network waits and tries
again later; a refusal the plugin cannot fix (a file the Mac does not take,
unpaired) is `failed` with the Mac's reason, for the tab to show. Nothing here
ever asks the Mac to print.

File names avoid "secret", "cert" and "conf": Orca's audit hook refuses them.
"""

from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from typing import Callable, List, Optional

from . import capture as capture_mod
from .transport import ShopError

BACKOFF = (10, 30, 60, 300, 900)
MAX_HASH_RETRIES = 1
GIVE_UP_AFTER = 7 * 24 * 3600


class Outbox:
    def __init__(self, root: str, clock: Callable[[], float] = time.time):
        self.root = root
        self.clock = clock
        os.makedirs(root, exist_ok=True)

    # -- items ----------------------------------------------------------------

    def _dir(self, item_id: str) -> str:
        return os.path.join(self.root, item_id)

    def _save(self, item: dict) -> None:
        path = os.path.join(self._dir(item["id"]), "item.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(item, f)
        os.replace(tmp, path)

    def items(self) -> List[dict]:
        out = []
        for name in sorted(os.listdir(self.root)):
            p = os.path.join(self.root, name, "item.json")
            try:
                with open(p, encoding="utf-8") as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda i: i["queuedAt"])

    def add(self, file_path: str, **capture_kw) -> dict:
        item_id = uuid.uuid4().hex[:16]
        os.makedirs(self._dir(item_id))
        name = os.path.basename(file_path)
        copy = os.path.join(self._dir(item_id), name)
        shutil.copyfile(file_path, copy)
        cap = capture_mod.from_file(copy, **capture_kw)
        item = {"id": item_id, "file": name, "capture": cap, "state": "new", "sliceId": None,
                "queuedAt": self.clock(), "nextAt": 0, "tries": 0, "hashRetries": 0, "reason": None}
        self._save(item)
        return item

    def forget(self, item_id: str) -> None:
        shutil.rmtree(self._dir(item_id), ignore_errors=True)

    # -- delivery -------------------------------------------------------------

    def due(self) -> Optional[dict]:
        now = self.clock()
        for item in self.items():
            if item["state"] in ("new", "announced") and item["nextAt"] <= now:
                return item
        return None

    def _wait(self, item: dict, seconds: Optional[int], reason: str) -> dict:
        item["tries"] += 1
        delay = seconds if seconds else BACKOFF[min(item["tries"] - 1, len(BACKOFF) - 1)]
        item["nextAt"] = self.clock() + delay
        item["reason"] = reason
        if self.clock() - item["queuedAt"] > GIVE_UP_AFTER:
            item["state"] = "failed"
            item["reason"] = "gave-up"
        return item

    def step(self, shop) -> Optional[dict]:
        """Move the next due item one step on. Returns it, or None when nothing is due."""
        item = self.due()
        if item is None:
            return None
        path = os.path.join(self._dir(item["id"]), item["file"])
        try:
            if item["state"] == "new":
                got = shop.announce(item["capture"])
                item["sliceId"] = got.get("sliceId")
                if got.get("duplicate") or got.get("state") == "in-inbox":
                    item["state"] = "done"
                else:
                    item["state"] = "announced"
                item["reason"], item["tries"] = None, 0   # progress: the next wait starts short
            else:
                shop.upload(item["sliceId"], path, item["capture"]["file"]["sha256"])
                item["state"] = "done"
                item["reason"] = None
        except ShopError as e:
            if e.status == 404 and e.reason == "unknown-slice":
                item["state"], item["sliceId"] = "new", None     # expired on the Mac: announce again
            elif e.status == 422 and e.reason == "hash-mismatch" and item["hashRetries"] < MAX_HASH_RETRIES:
                item["hashRetries"] += 1                           # the bytes changed in flight: once more
            elif e.status in (429, 503, 507) or e.reason in ("unsigned-answer",):
                self._wait(item, e.retry_after, e.reason)
            elif e.status == 401 and e.reason in ("device-clock",):
                self._wait(item, 5, e.reason)
            else:
                item["state"], item["reason"] = "failed", e.reason
        except OSError as e:
            self._wait(item, None, "offline" if not isinstance(e, FileNotFoundError) else "file-gone")
            if isinstance(e, FileNotFoundError) and not os.path.exists(path):
                item["state"] = "failed"
        if item["state"] == "done":
            item["doneAt"] = self.clock()
        self._save(item)
        return item

    def drain(self, shop, limit: int = 20) -> List[dict]:
        """Steps until nothing is due (or `limit` steps): what the worker thread runs."""
        moved = []
        for _ in range(limit):
            item = self.step(shop)
            if item is None:
                break
            moved.append(item)
        return moved
