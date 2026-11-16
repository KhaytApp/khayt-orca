"""The slicer's own figures, read from the head and tail of a sliced file.

A port of Khayt's `lib/gcode-parse.js` (`parseFilaments`, `parseFilamentGrams`,
`parsePrintTimeMins`), held to the same shared vectors
(tests/vectors/connector.vectors.json, a copy of Khayt's docs/). The Mac reads
the file again itself and its reading wins: these figures are "what the slicer
said", for the capture.

Only the first and last 512 KB are read: the summary lives there, and a
regular expression over a whole gigabyte of moves is slow for nothing.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

EDGE = 512 * 1024
_FLAGS = re.IGNORECASE | re.MULTILINE | re.ASCII


def head_and_tail(path: str, edge: int = EDGE) -> str:
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size <= 2 * edge:
            data = f.read()
        else:
            head = f.read(edge)
            f.seek(size - edge)
            data = head + b"\n" + f.read(edge)
    return data.decode("utf-8", errors="replace")


def _comment_value(text: str, key_pattern: str) -> Optional[str]:
    m = re.search(r"^[ \t]*;[ \t]*" + key_pattern + r"[ \t]*[:=][ \t]*(.*)$", text, _FLAGS)
    return m.group(1).rstrip("\r").strip() if m else None


_NUMBER = re.compile(r"^\d+(\.\d+)?$|^\.\d+$", re.ASCII)


def _number_list(value: Optional[str]) -> list:
    if not value:
        return []
    out = []
    for part in re.split(r"[,;]", value):
        t = part.strip()
        out.append(float(t) if _NUMBER.match(t) else None)
    return out


def _string_list(value: Optional[str]) -> list:
    if not value:
        return []
    out, cur, quoted, i = [], "", False, 0
    while i < len(value):
        c = value[i]
        if quoted:
            if c == "\\" and i + 1 < len(value):
                i += 1
                cur += value[i]
            elif c == '"':
                quoted = False
            else:
                cur += c
        elif c == '"':
            quoted = True
        elif c == ";":
            out.append(cur.strip())
            cur = ""
        else:
            cur += c
        i += 1
    out.append(cur.strip())
    return out


def _at(lst: list, i: int):
    v = lst[i] if i < len(lst) else None
    return None if v is None or v == "" else v


def _colour(v) -> Optional[str]:
    t = str(v or "").strip()
    return t.upper() if re.fullmatch(r"#([0-9a-f]{6}|[0-9a-f]{8})", t, re.IGNORECASE) else None


def _num(v):
    """A whole number as an int, as JSON.parse gives it back."""
    return int(v) if isinstance(v, float) and v.is_integer() else v


def filaments(text: str) -> List[dict]:
    """Per slot, for the slots that used any; `slot` keeps its position (1-based)."""
    grams = _number_list(_comment_value(text, r"filament used \[g\]"))
    mm = _number_list(_comment_value(text, r"filament used \[mm\]"))
    cm3 = _number_list(_comment_value(text, r"filament used \[cm3\]"))
    if not cm3:
        cm3 = [None if v is None else v / 1000 for v in _number_list(_comment_value(text, r"filament used \[mm3\]"))]
    if not grams:
        grams = _number_list(_comment_value(text, r"total filament weight \[g\]"))
    if not mm:
        mm = _number_list(_comment_value(text, r"total filament length \[mm\]"))
    count = max(len(grams), len(mm), len(cm3))
    if not count:
        return []
    cost = _number_list(_comment_value(text, "filament cost"))
    types = _string_list(_comment_value(text, "filament_type"))
    colours = _string_list(_comment_value(text, "filament_colour"))
    presets = _string_list(_comment_value(text, "filament_settings_id"))
    density = _number_list(_comment_value(text, "filament_density"))
    per_kg = _number_list(_comment_value(text, "filament_cost"))
    out = []
    for i in range(count):
        g, length, v = _at(grams, i), _at(mm, i), _at(cm3, i)
        if not ((g or 0) > 0 or (length or 0) > 0 or (v or 0) > 0):
            continue
        kind = _at(types, i)
        d, k = _at(density, i), _at(per_kg, i)
        out.append({
            "slot": i + 1,
            "grams": _num(g) if (g or 0) > 0 else None,
            "cm3": _num(v) if (v or 0) > 0 else None,
            "mm": _num(length) if (length or 0) > 0 else None,
            "cost": _num(_at(cost, i)),
            "type": str(kind).upper() if kind else None,
            "colour": _colour(_at(colours, i)),
            "preset": _at(presets, i),
            "density": _num(d) if (d or 0) > 0 else None,
            "costPerKg": _num(k) if (k or 0) > 0 else None,
        })
    return out


def _js_float(n: str) -> float:
    """JavaScript's `parseFloat(n) || 0` on a run of digits and dots."""
    m = re.match(r"\d+\.?\d*|\.\d+", n)
    return float(m.group()) if m else 0.0


def _sum_list(s: str) -> Optional[float]:
    total = sum(_js_float(n) for n in re.findall(r"[\d.]+", s, re.ASCII))
    return total if total > 0 else None


def filament_grams(text: str) -> Optional[float]:
    for pattern in (r"total filament used \[g\]\s*[:=]\s*([\d.,\s]+)",
                    r"total filament weight \[g\]\s*[:=]\s*([\d.,\s]+)",
                    r"(?<!total )filament used \[g\]\s*[:=]\s*([\d.,\s]+)"):
        m = re.search(pattern, text, re.IGNORECASE | re.ASCII)
        if m:
            v = _sum_list(m.group(1))
            if v is not None:
                return v
    m = re.search(r"FILAMENT_WEIGHT\s*[:=]\s*([\d.]+)", text, re.IGNORECASE | re.ASCII)
    if m:
        try:
            v = float(m.group(1))
            if v > 0:
                return v
        except ValueError:
            pass
    return None


def print_seconds(text: str) -> Optional[int]:
    for pattern in (r"estimated printing time[^=\n\r]*=\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?",
                    r"total estimated time\s*[:=]\s*(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?"):
        m = re.search(pattern, text, re.IGNORECASE | re.ASCII)
        if m and any(m.groups()):
            d, h, mi, s = (int(x or 0) for x in m.groups())
            return d * 86400 + h * 3600 + mi * 60 + s
    m = re.search(r"^;\s*TIME:\s*(\d+)", text, _FLAGS)
    return int(m.group(1)) if m else None


def _count(text: str, key: str) -> Optional[int]:
    v = _comment_value(text, key)
    return int(v) if v and v.isdigit() else None


def figures(path: str) -> dict:
    """What the capture carries about a file: time, layers, tool changes, slots."""
    text = head_and_tail(path)
    out = {"timeSec": print_seconds(text), "layers": _count(text, "total layers count"),
           "toolchanges": _count(text, r"total filament change"), "filaments": filaments(text)}
    return {k: v for k, v in out.items() if v is not None}
