"""The shop's price for the last sliced plate: what is asked, and what of the answer is believed.

The plugin does NO arithmetic. It sends the slice facts and a quantity to the
Mac (`POST /api/connectors/price`, Khayt's docs/LAN_API.md), which prices them
with its own code (`lib/connector-price.js`) and answers sealed. This module
only picks the facts out of the capture and checks the answer key by key: a
figure the Mac did not send is never made up here, and an answer that does not
fit is no price at all, never a guess.

Nothing here touches disk. The price lives in the controller's memory, and is
dropped whenever it might no longer be true (a new plate, a new quantity, a
failed ask). Plan: ~/Khayt-proposals/orca-tab-price-plan.md (approved).
"""

from __future__ import annotations

import math
import re
from typing import Iterable, Optional

MIN_QTY, MAX_QTY = 1, 999
MAX_FILAMENTS = 16
MAX_GRAMS = 100000
UNPRICED = ("no-material", "no-currency", "bad-facts")
BASIS = ("measured", "assumed")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def clamp_qty(qty) -> int:
    try:
        q = int(qty)
    except (TypeError, ValueError):
        return MIN_QTY
    return max(MIN_QTY, min(MAX_QTY, q))


def _number(v, lo: float = 0, hi: float = math.inf) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi


def facts(capture: Optional[dict]) -> Optional[dict]:
    """The plate's facts from what the capture read out of the G-code (field names as
    `lib/connector-slice.js`), or None when there is nothing to price: no print time,
    or no filament with a weight (a binary G-code or a .gcode.3mf has no text summary)."""
    if not isinstance(capture, dict):
        return None
    time_sec = capture.get("timeSec")
    if not _number(time_sec) or time_sec <= 0:
        return None
    rows = []
    for f in capture.get("filaments") or []:
        if not isinstance(f, dict) or not _number(f.get("grams"), 0, MAX_GRAMS) or f["grams"] <= 0:
            continue
        row = {"grams": f["grams"]}
        for key in ("slot", "type", "colour"):
            if f.get(key) is not None:
                row[key] = f[key]
        rows.append(row)
    if not rows or len(rows) > MAX_FILAMENTS:
        return None
    out = {"timeSec": time_sec, "filaments": rows}
    for key in ("layers", "toolchanges"):
        if _number(capture.get(key)):
            out[key] = capture[key]
    for key in ("printer", "process"):
        preset = (capture.get(key) or {}).get("preset") if isinstance(capture.get(key), dict) else None
        if isinstance(preset, str) and preset:
            out[key] = {"preset": preset}
    return out


def request(plate: dict, qty: int) -> dict:
    """What goes to the Mac: the facts, a version and the quantity. No file, no names."""
    return {"v": 1, "qty": clamp_qty(qty), **plate}


def first_type(plate: Optional[dict]) -> Optional[str]:
    """The plate's first filament type, for "Add a PLA spool in Khayt"."""
    for f in (plate or {}).get("filaments", []):
        if isinstance(f.get("type"), str) and f["type"]:
            return f["type"]
    return None


def read(answer, qty: int, scopes: Iterable[str], plate: Optional[dict] = None) -> Optional[dict]:
    """The tab's price block from the Mac's answer, or None: no block at all.

    - `{"prices":"off"}`, or anything that is not exactly "on": no block.
    - `{"prices":"on","unpriced":…}`: a line saying why, never a figure.
    - `{"prices":"on", currency, qty, unit, total, rounded, basis}`: the price. An
      answer for another quantity than the one asked is dropped (it is stale).
    - `cost` and `margin` only when the Mac sent them AND this device holds `cost:read`.
    """
    if not isinstance(answer, dict) or answer.get("prices") != "on":
        return None
    if "unpriced" in answer:
        why = answer.get("unpriced")
        if why not in UNPRICED:
            return None
        out = {"state": "unpriced", "reason": why, "qty": qty}
        if why == "no-material" and first_type(plate):
            out["type"] = first_type(plate)
        return out
    currency = answer.get("currency")
    if not isinstance(currency, str) or not _CURRENCY.match(currency):
        return None
    if answer.get("qty") != qty or isinstance(answer.get("qty"), bool):
        return None
    unit, total = answer.get("unit"), answer.get("total")
    if not _number(unit) or not _number(total):
        return None
    out = {"state": "on", "qty": qty, "currency": currency, "unit": unit, "total": total,
           "rounded": answer.get("rounded") is True,
           # An unknown basis reads as "assumed": the footnote then says some rates are.
           "basis": answer.get("basis") if answer.get("basis") in BASIS else "assumed"}
    if "cost:read" in set(scopes or ()):
        cost = answer.get("cost")
        if isinstance(cost, dict) and _number(cost.get("unit")) and _number(cost.get("total")):
            out["cost"] = {"unit": cost["unit"], "total": cost["total"]}
        margin = answer.get("margin")
        if isinstance(margin, dict):
            # The frames show "amount · % on cost" (Khayt's cost-plus convention). The
            # Mac adds `amount` and `pctOnCost` after #2595; until then only `pct`
            # (of the price) comes, and the tab says so. Never computed here.
            kept = {k: margin[k] for k in ("pct", "pctOnCost", "amount") if _number(margin.get(k), -math.inf)}
            if "pct" in kept or "pctOnCost" in kept:
                out["margin"] = kept
    # "rounded to 0.50" once the Mac sends the step it rounded to; "rounded" until then.
    rnd = answer.get("round")
    if out["rounded"] and isinstance(rnd, dict) and _number(rnd.get("step")) and rnd["step"] > 0:
        out["round"] = {"step": rnd["step"]}
    return out
