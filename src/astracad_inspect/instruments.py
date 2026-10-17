"""Instrument register: what a measurement was taken with, and whether that was allowed.

One JSON file per workspace. Each instrument has an ID, kind, unit, measuring range, resolution
and calibration due date. Changes are appended to the instrument's own history (who, when, what),
so the register shows how an instrument came to be in its present state.

When a measurement is recorded with an instrument, the register is consulted and the value is
REFUSED, with the reason, when:
    * the instrument is not in the register (once the register has any entry),
    * it is retired,
    * its calibration was due before the day of the measurement,
    * the value is outside its measuring range,
    * the value is written finer than the instrument can resolve (100.004 from a 0.01 caliper).
It is recorded WITH A WARNING when the resolution is coarser than a tenth of the tolerance band
(the common 10:1 rule of thumb for gauge selection): the reading is real, but the instrument is a
weak choice for that characteristic. Nothing here edits a value.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import date
from pathlib import Path

KINDS = ("caliper", "micrometer", "height gauge", "dial indicator", "CMM", "bore gauge",
         "pin gauge", "thread gauge", "gauge block", "protractor", "optical comparator", "other")
UNIT_MM = {"mm": 1.0, "in": 25.4, "um": 0.001, "cm": 10.0, "m": 1000.0, "deg": None}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
_NAME = re.compile(r"[\w .'-]{1,64}")
_lock = threading.Lock()


def _num(v, name, positive=False):
    try:
        x = float(str(v).strip().replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(x) or (positive and x <= 0):
        raise ValueError(f"{name} must be a {'positive ' if positive else ''}finite number")
    return x


def load(path: Path) -> dict:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"instruments": {}}


def _save(path: Path, reg: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def upsert(path: Path, body: dict) -> dict:
    """Add an instrument or change one. Every change is attributed and kept in its history."""
    by = str(body.get("by", "")).strip()
    if not _NAME.fullmatch(by):
        raise ValueError("your name is required to change the register")
    iid = str(body.get("id", "")).strip()
    if not _ID.fullmatch(iid):
        raise ValueError("instrument ID: letters, digits . _ - (1-32), starting with a letter or digit")
    kind = body.get("kind") or "other"
    if kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
    unit = body.get("unit") or "mm"
    if unit not in UNIT_MM:
        raise ValueError(f"unit must be one of: {', '.join(UNIT_MM)}")
    lo = _num(body.get("range_min", 0), "range minimum")
    hi = _num(body.get("range_max"), "range maximum")
    if hi <= lo:
        raise ValueError("range maximum must be above the minimum")
    res = _num(body.get("resolution"), "resolution", positive=True)
    due = str(body.get("calibration_due", "")).strip()
    try:
        date.fromisoformat(due)
    except ValueError:
        raise ValueError("calibration due date must be YYYY-MM-DD") from None
    rec = {"id": iid, "description": str(body.get("description") or "")[:200], "kind": kind,
           "unit": unit, "range_min": lo, "range_max": hi, "resolution": res,
           "calibration_due": due, "calibration_ref": str(body.get("calibration_ref") or "")[:80],
           "status": "retired" if body.get("status") == "retired" else "active"}
    with _lock:
        reg = load(path)
        old = reg["instruments"].get(iid)
        hist = (old or {}).get("history", [])
        changed = {k: [old.get(k), v] for k, v in rec.items() if old and old.get(k) != v}
        if old and not changed:
            return old
        hist.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "by": by,
                     "action": "changed" if old else "added", "changes": changed})
        reg["instruments"][iid] = {**rec, "history": hist}
        _save(path, reg)
        return reg["instruments"][iid]


def status(inst: dict, today: date | None = None) -> dict:
    today = today or date.today()
    if inst.get("status") == "retired":
        return {"state": "RETIRED", "text": "retired"}
    due = date.fromisoformat(inst["calibration_due"])
    days = (due - today).days
    if days < 0:
        return {"state": "EXPIRED", "text": f"calibration expired {-days} day(s) ago ({due})", "days": days}
    if days <= 30:
        return {"state": "DUE_SOON", "text": f"calibration due in {days} day(s) ({due})", "days": days}
    return {"state": "CALIBRATED", "text": f"calibrated until {due}", "days": days}


def _to(value: float, from_unit: str, to_unit: str) -> float | None:
    if from_unit == to_unit:
        return value
    a, b = UNIT_MM.get(from_unit), UNIT_MM.get(to_unit)
    if a is None or b is None:
        return None
    return value * a / b


def check(reg: dict, instrument_id: str, value: float | None, row: dict,
          on: date | None = None) -> tuple[dict | None, list[str]]:
    """(snapshot of the instrument for the record, warnings). Raises ValueError to refuse."""
    insts = reg.get("instruments") or {}
    iid = (instrument_id or "").strip()
    if not iid:
        return None, ["no instrument recorded for this value"] if insts else []
    if iid not in insts:
        if insts:
            raise ValueError(f"instrument {iid!r} is not in the instrument register")
        return None, [f"instrument {iid!r} recorded as text: the register is empty"]
    inst = insts[iid]
    st = status(inst, on)
    if st["state"] in ("RETIRED", "EXPIRED"):
        raise ValueError(f"instrument {iid} cannot be used: {st['text']}")
    warnings = []
    unit = row.get("unit") or inst["unit"]
    if value is not None:
        v = _to(value, unit, inst["unit"])
        if v is None:
            warnings.append(f"range and resolution not checked: {unit} vs instrument unit {inst['unit']}")
        else:
            if not (inst["range_min"] - 1e-9 <= v <= inst["range_max"] + 1e-9):
                raise ValueError(f"{value:g} {unit} is outside {iid}'s range "
                                 f"{inst['range_min']:g}..{inst['range_max']:g} {inst['unit']}")
            if unit == inst["unit"]:
                # same unit: the reading must sit on the instrument's own step grid
                steps = v / inst["resolution"]
                fine = abs(steps - round(steps)) > 1e-6 * max(1.0, abs(steps))
            else:
                # converted reading: it cannot carry more decimals than the resolution supports there
                txt = repr(float(value))
                dec = len(txt.split(".")[1]) if "." in txt and "e" not in txt else 0
                fine = 10.0 ** -dec < 0.5 * _to(inst["resolution"], inst["unit"], unit)
            if fine:
                raise ValueError(f"{value:g} is written finer than {iid} can resolve "
                                 f"({inst['resolution']:g} {inst['unit']}); record what the instrument read")
    lo, hi = row.get("minimum"), row.get("maximum")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and hi > lo:
        band = _to(hi - lo, unit, inst["unit"])
        if band is not None and inst["resolution"] > band / 10 + 1e-12:
            warnings.append(f"{iid} resolution {inst['resolution']:g} {inst['unit']} is coarser than a tenth "
                            f"of the tolerance band ({band:g} {inst['unit']}): a weak instrument for this check")
    if st["state"] == "DUE_SOON":
        warnings.append(f"{iid}: {st['text']}")
    snap = {k: inst[k] for k in ("id", "kind", "unit", "resolution", "calibration_due", "calibration_ref")}
    return snap, warnings
