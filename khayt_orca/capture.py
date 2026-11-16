"""What the plugin tells the shop about a slice before it sends the file.

The shape is Khayt's `lib/connector-slice.js` (shared vectors:
tests/vectors/connector-capture.vectors.json). The Mac checks it field by
field and keeps it only as a SUGGESTION beside the inbox item: it reads the
file's own figures itself, and nothing here creates a job, a quote or a print.

Never put a customer here: there is no field for one, and the Mac drops any
key it does not know.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Iterable, Optional

from .transport import file_sha256

INTENTS = ("inbox", "quote", "job", "attach", "print")
MAX_THUMB_BYTES = 48 * 1024


def file_type(name: str) -> Optional[str]:
    lower = name.lower()
    if lower.endswith(".gcode.3mf"):
        return "gcode.3mf"
    ext = lower.rsplit(".", 1)[-1] if "." in lower else ""
    if ext == "bgcode":
        return "bgcode"
    if ext in ("gcode", "gco", "g"):
        return "gcode"
    return None


def build(file_path: str, *, intent: str = "inbox", job_id: Optional[str] = None,
          machine_id: Optional[str] = None, start_print: bool = False, slicer: Optional[dict] = None,
          printer: Optional[dict] = None, process: Optional[dict] = None, plate: Optional[dict] = None,
          time_sec: Optional[int] = None, layers: Optional[int] = None, toolchanges: Optional[int] = None,
          filaments: Optional[Iterable[dict]] = None, thumbnail: Optional[dict] = None,
          sha256: Optional[str] = None, now: Optional[datetime] = None) -> dict:
    name = os.path.basename(file_path)
    kind = file_type(name)
    if kind is None:
        raise ValueError(f"{name}: Khayt takes G-code, binary G-code or a sliced .gcode.3mf")
    if intent not in INTENTS:
        raise ValueError(f"intent {intent!r}")
    digest = sha256 or file_sha256(file_path)
    at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {
        "v": 1, "kind": "slice", "id": secrets.token_hex(8), "idem": "sha256:" + digest, "at": at,
        "intent": intent,
        "file": {"name": name, "bytes": os.path.getsize(file_path), "sha256": digest, "type": kind},
    }
    if intent == "attach" and job_id:
        out["jobId"] = job_id
    if intent == "print" and machine_id:
        out["machineId"] = machine_id
        out["startPrint"] = bool(start_print)  # kept as asked; v1 never starts a print
    for key, value in (("slicer", slicer), ("printer", printer), ("process", process), ("plate", plate),
                       ("timeSec", time_sec), ("layers", layers), ("toolchanges", toolchanges)):
        if value is not None:
            out[key] = value
    if filaments is not None:
        out["filaments"] = [f for f in filaments if f.get("grams")]
    if thumbnail is not None and len(thumbnail.get("b64", "")) * 3 // 4 <= MAX_THUMB_BYTES:
        out["thumbnail"] = thumbnail
    return out


def from_file(file_path: str, **kw) -> dict:
    """`build`, with the slicer's own time, layers, tool changes and slots read from the file
    (`gcode.figures`). A binary G-code or a .gcode.3mf carries no text summary to read."""
    from . import gcode
    if file_type(os.path.basename(file_path)) == "gcode":
        for key, value in gcode.figures(file_path).items():
            kw.setdefault({"timeSec": "time_sec"}.get(key, key), value)
    return build(file_path, **kw)
