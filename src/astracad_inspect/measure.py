"""Measurement results against an inspection plan.

What an inspector measured is a human record, so this module never produces a value: it stores
values a named inspector entered, and CALCULATES only what follows from them and the drawing's own
limits - a verdict per measurement, the state of each sample, and summary statistics.

Records are append-only (``<stem>.measurements.jsonl`` next to the plan). A correction is a new
entry for the same sample and characteristic; it supersedes the earlier one, which stays in the
log. Withdrawing a value is also an entry, and needs a reason.

Verdicts:
    PASS / FAIL       the value against the row's limits (inclusive, as the plan states)
    NOT_EVALUABLE     the drawing gives no limits for this row: the value is recorded, never judged
A sample is COMPLETE only when every characteristic that is not rejected has a judged value; it is
ACCEPTABLE only when it is complete, nothing failed AND the plan itself is releasable - a part
cannot be accepted against a plan that still has open findings.

Capability (Cp, Cpk) is computed when a characteristic has two limits and at least two samples
with spread; it is labelled with its sample count because a handful of parts is an indication,
not a capability study.
"""
from __future__ import annotations

import json
import math
import re
import secrets
import statistics
import threading
import time
from pathlib import Path

_NAME = re.compile(r"[\w .'-]{1,64}")
_SAMPLE = re.compile(r"[\w .#/-]{1,40}")
_lock = threading.Lock()
EPS = 1e-9


def log_path(plan_path: Path) -> Path:
    stem = plan_path.name[:-len(".plan.json")]
    return plan_path.with_name(f"{stem}.measurements.jsonl")


def read_log(plan_path: Path) -> list[dict]:
    p = log_path(plan_path)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def record(plan_path: Path, plan: dict, body: dict, register: dict | None = None) -> dict:
    """Validate and append one entry. Raises ValueError with the reason when it is refused.
    With an instrument `register`, the instrument is checked (see instruments.check) and a snapshot
    of it - resolution, calibration due date and certificate - is stored with the value."""
    entry = build_entry(plan, body, register)
    append(plan_path, [entry])
    return entry


def append(plan_path: Path, entries: list[dict]) -> None:
    """Append already-validated entries in one write (an import lands whole or not at all)."""
    with _lock:
        with log_path(plan_path).open("a", encoding="utf-8") as f:
            f.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))


def build_entry(plan: dict, body: dict, register: dict | None = None) -> dict:
    """The validated entry for `body`, not yet written. Raises ValueError with the reason."""
    inspector = str(body.get("inspector", "")).strip()
    if not _NAME.fullmatch(inspector):
        raise ValueError("inspector name required (letters, digits, space . ' - ; up to 64)")
    sample = str(body.get("sample", "")).strip()
    if not _SAMPLE.fullmatch(sample):
        raise ValueError("sample / serial number required (letters, digits, space . # / - ; up to 40)")
    cid = str(body.get("cid", ""))
    rows = {r["cid"]: r for r in plan.get("rows") or []}
    if cid not in rows:
        raise ValueError(f"unknown characteristic {cid!r}")
    entry = {"id": secrets.token_hex(6), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "inspector": inspector, "sample": sample, "cid": cid,
             "balloon_no": rows[cid].get("balloon_no"), "unit": rows[cid].get("unit"),
             "instrument": str(body.get("instrument") or "").strip()[:80],
             "note": str(body.get("note") or "").strip()[:500]}
    if body.get("withdraw"):
        if not entry["note"]:
            raise ValueError("withdrawing a measurement needs a reason (note)")
        entry.update(value=None, withdrawn=True)
    else:
        v = body.get("value")
        try:
            x = float(str(v).strip().replace(",", "."))
        except (TypeError, ValueError):
            raise ValueError("value must be a number in the characteristic's unit") from None
        if not math.isfinite(x) or abs(x) > 1e6:
            raise ValueError("value must be a finite number")
        entry["value"] = x
    if register is not None and not entry.get("withdrawn"):
        from .instruments import check
        snap, warnings = check(register, entry["instrument"], entry["value"], rows[cid])
        if snap:
            entry["instrument_record"] = snap
        if warnings:
            entry["warnings"] = warnings
    # imported values say where they came from (file hash + row) and when they were measured
    for k in ("source", "measured_at", "converted_from"):
        if body.get(k):
            entry[k] = body[k]
    return entry


def verdict(row: dict, value: float | None) -> tuple[str, str]:
    lo, hi = row.get("minimum"), row.get("maximum")
    if value is None:
        return "WITHDRAWN", "value withdrawn"
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        return "NOT_EVALUABLE", "the drawing states no limits for this characteristic; recorded, not judged"
    tol = EPS * max(1.0, abs(lo), abs(hi))
    if lo - tol <= value <= hi + tol:
        return "PASS", f"{value:g} within {lo:g} .. {hi:g}"
    side = "below the lower" if value < lo else "above the upper"
    dev = (lo - value) if value < lo else (value - hi)
    return "FAIL", f"{value:g} is {dev:g} {side} limit ({lo:g} .. {hi:g})"


def evaluate(plan: dict, log: list[dict]) -> dict:
    rows = plan.get("rows") or []
    by_cid = {r["cid"]: r for r in rows}
    latest: dict[tuple[str, str], dict] = {}
    history: dict[tuple[str, str], list[dict]] = {}
    for e in log:                                    # file order = time order
        if e.get("cid") not in by_cid:
            continue
        k = (e["sample"], e["cid"])
        if k in latest:
            latest[k] = {**latest[k], "superseded_by": e["id"]}
            history.setdefault(k, []).append(latest[k])
        latest[k] = e
    samples = sorted({s for s, _ in latest}, key=lambda s: min(e["at"] for (ss, _), e in latest.items() if ss == s))
    cells = {}
    for (s, cid), e in latest.items():
        v, why = verdict(by_cid[cid], e.get("value"))
        cells[f"{s}|{cid}"] = {**e, "verdict": v, "why": why,
                               "history": [h["id"] for h in history.get((s, cid), [])]}
    required = [r["cid"] for r in rows if r.get("status") != "rejected"]
    sample_state = {}
    for s in samples:
        vs = [cells.get(f"{s}|{cid}", {}).get("verdict") for cid in required]
        fails = [cid for cid, v in zip(required, vs) if v == "FAIL"]
        missing = [cid for cid, v in zip(required, vs) if v in (None, "WITHDRAWN")]
        unjudged = [cid for cid, v in zip(required, vs) if v == "NOT_EVALUABLE"]
        complete = not missing and not unjudged
        if fails:
            state = "FAIL"
        elif not complete:
            state = "INCOMPLETE"
        elif not plan.get("approvable"):
            state = "NOT_ACCEPTABLE_PLAN_OPEN"
        else:
            state = "ACCEPTABLE"
        sample_state[s] = {"state": state, "measured": len(required) - len(missing),
                           "required": len(required), "fails": fails, "missing": missing,
                           "not_evaluable": unjudged}
    capability = {}
    for r in rows:
        vals = [cells[f"{s}|{r['cid']}"]["value"] for s in samples
                if f"{s}|{r['cid']}" in cells and cells[f"{s}|{r['cid']}"].get("value") is not None]
        if not vals:
            continue
        c = {"n": len(vals), "mean": statistics.fmean(vals), "min": min(vals), "max": max(vals)}
        lo, hi = r.get("minimum"), r.get("maximum")
        if len(vals) >= 2:
            sd = statistics.stdev(vals)
            c["stdev"] = sd
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                if sd > 0:
                    c["cp"] = (hi - lo) / (6 * sd)
                    c["cpk"] = min(hi - c["mean"], c["mean"] - lo) / (3 * sd)
                    c["note"] = (f"estimate from {len(vals)} sample(s); a capability study needs a "
                                 "planned, larger sample from a stable process")
                else:
                    c["note"] = "no spread between samples: Cp/Cpk undefined"
        capability[r["cid"]] = c
    return {"samples": samples, "cells": cells, "sample_state": sample_state,
            "capability": capability, "entries": len(log)}


def write_results(plan: dict, log: list[dict], out_xlsx: Path) -> Path:
    """Results workbook: the matrix (balloon x sample), capability, and the full append-only log."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    ev = evaluate(plan, log)
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append([f"Measurement results - {Path(plan.get('source_document') or '').name} rev {plan.get('revision')}"])
    ws.append(["Plan gate", plan.get("gate")])
    ws.append(["Source SHA-256", plan.get("source_sha256")])
    ws.append([])
    head = ["Balloon", "Characteristic", "Requirement", "Unit", "Lower limit", "Upper limit"]
    for s in ev["samples"]:
        head += [f"{s} value", f"{s} verdict"]
    ws.append(head)
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    fills = {"PASS": "E3F4EC", "FAIL": "FCE8EB", "NOT_EVALUABLE": "EEF0F3"}
    for r in plan.get("rows") or []:
        line = [r.get("balloon_no"), r["cid"], r.get("callout"), r.get("unit"), r.get("minimum"), r.get("maximum")]
        for s in ev["samples"]:
            cell = ev["cells"].get(f"{s}|{r['cid']}")
            line += [cell.get("value") if cell else None, cell["verdict"] if cell else "not measured"]
        ws.append(line)
        for k, s in enumerate(ev["samples"]):
            cell = ev["cells"].get(f"{s}|{r['cid']}")
            if cell and cell["verdict"] in fills:
                ws.cell(ws.max_row, 8 + 2 * k).fill = PatternFill("solid", fgColor=fills[cell["verdict"]])
    ws.append([])
    ws.append(["Sample", "State", "Measured", "Required", "Failed", "Missing", "Not evaluable"])
    for s, st in ev["sample_state"].items():
        ws.append([s, st["state"], st["measured"], st["required"], ", ".join(st["fails"]),
                   ", ".join(st["missing"]), ", ".join(st["not_evaluable"])])
    cap = wb.create_sheet("Capability")
    cap.append(["Characteristic", "n", "Mean", "Std dev", "Min", "Max", "Cp", "Cpk", "Note"])
    for cid, c in ev["capability"].items():
        cap.append([cid, c["n"], c["mean"], c.get("stdev"), c["min"], c["max"], c.get("cp"), c.get("cpk"), c.get("note", "")])
    lg = wb.create_sheet("Log")
    keys = ["id", "at", "inspector", "sample", "balloon_no", "cid", "value", "unit", "instrument", "note", "withdrawn"]
    lg.append(keys + ["instrument resolution", "calibration due", "calibration ref", "warnings"])
    for e in log:
        ir = e.get("instrument_record") or {}
        lg.append([e.get(k) for k in keys] + [ir.get("resolution"), ir.get("calibration_due"),
                                              ir.get("calibration_ref"), "; ".join(e.get("warnings") or [])])
    out_xlsx = Path(out_xlsx)
    wb.save(out_xlsx)
    return out_xlsx


def verify_results(plan: dict, log: list[dict], xlsx: Path) -> list[tuple[str, bool, str]]:
    """Re-open: the log sheet holds every entry, and every judged cell matches a fresh evaluation."""
    from openpyxl import load_workbook
    wb = load_workbook(xlsx, read_only=True)
    lg = [r for r in wb["Log"].iter_rows(min_row=2, values_only=True) if any(v is not None for v in r)]
    checks = [("log_rows", len(lg) == len(log), f"{len(lg)}/{len(log)}")]
    ev = evaluate(plan, log)
    ids = {r[0] for r in lg}
    checks.append(("log_ids_match", ids == {e["id"] for e in log}, f"{len(ids)} ids"))
    ws = wb["Results"]
    rows = list(ws.iter_rows(min_row=6, values_only=True))
    bad = []
    for r in plan.get("rows") or []:
        line = next((x for x in rows if x and x[1] == r["cid"]), None)
        for k, s in enumerate(ev["samples"]):
            cell = ev["cells"].get(f"{s}|{r['cid']}")
            want = cell["verdict"] if cell else "not measured"
            if line is None or line[7 + 2 * k] != want:
                bad.append(f"{s}/{r['cid']}")
    checks.append(("verdicts_match", not bad, "all match" if not bad else f"differ: {bad[:5]}"))
    return checks
