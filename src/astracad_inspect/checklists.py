"""Evidence-linked checklists for an inspection run.

A checklist item is either AUTO - ASTraCAD evaluates it from the run's own records (plan checks,
export read-back, measurement log, instrument register) and shows the evidence - or MANUAL, which
only a person can answer. Either way the DECISION is a person's: every item needs a named PASS,
FAIL or N/A (N/A and FAIL need a note), and an AUTO item cannot be passed while its evidence says
it fails. The list is complete when every item is decided and none failed; then it can be signed.

Decisions are append-only (``<stem>.checklist_<template>[_<sample>].jsonl``); the latest decision per
item counts, earlier ones stay as history. The evidence an AUTO item showed is stored with each
decision, so a later reader sees what the decider saw.

Templates are ASTraCAD's own wording, written for this product; none is copied from another
checklist product.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date
from pathlib import Path

_NAME = re.compile(r"[\w .'-]{1,64}")
_lock = threading.Lock()

# ---- automatic evidence -----------------------------------------------------------------------
# each returns (state, evidence) with state PASS / FAIL / NOT_APPLICABLE


def _check(plan, name):
    return next((c for c in plan.get("checks") or [] if c.get("name") == name), None)


def _units(ctx):
    p = ctx["plan"]
    return ("PASS", f"unit {p.get('unit')}") if p.get("unit") else ("FAIL", "no unit stated or declared")


def _revision(ctx):
    p = ctx["plan"]
    src = p.get("revision_source") or ""
    if src.startswith("default"):
        return "FAIL", f"revision {p.get('revision')} is a default ({src}); the drawing did not state it"
    return "PASS", f"revision {p.get('revision')} ({src or 'stated'})"


def _title_block(ctx):
    tb = {k: v for k, v in (ctx["plan"].get("title_block") or {}).items() if not k.startswith("_") and v}
    if tb.get("drawing_number") or tb.get("part_name"):
        return "PASS", ", ".join(f"{k}: {v}" for k, v in tb.items())
    return "FAIL", "no drawing number or part name recognised in the title block"


def _all_limits(ctx):
    rows = [r for r in ctx["plan"].get("rows") or [] if r.get("status") != "rejected"]
    open_ = [str(r.get("balloon_no")) for r in rows if r.get("minimum") is None and r.get("kind") != "angular"]
    if not open_:
        return "PASS", f"all {len(rows)} characteristics have limits"
    return "FAIL", f"balloons without limits (no tolerance, no general tolerance applied): {', '.join(open_[:12])}"


def _interpreted(ctx):
    n = len(ctx["plan"].get("uninterpreted") or [])
    return ("PASS", "no escalated annotations") if n == 0 else ("FAIL", f"{n} annotation(s) escalated and not resolved")


def _no_blocking(ctx):
    rows = ctx["plan"].get("rows") or []
    b = [str(r.get("balloon_no")) for r in rows if r.get("blocking")]
    return ("PASS", "no blocking findings") if not b else ("FAIL", f"blocking findings on balloons {', '.join(b)}")


def _ballooning(ctx):
    p = ctx["plan"]
    names = ["every_characteristic_ballooned", "balloons_do_not_overlap", "leaders_do_not_cross",
             "balloons_clear_of_drawing", "balloons_clear_of_part"]
    got = [(n, _check(p, n)) for n in names if _check(p, n)]
    bad = [n for n, c in got if c.get("outcome") != "passed"]
    if not got:
        return "NOT_APPLICABLE", "the plan records no layout checks"
    return ("PASS", "; ".join(f"{n}: {c.get('actual')}" for n, c in got)) if not bad else \
        ("FAIL", "failed: " + ", ".join(bad))


def _exports(ctx):
    ver = (ctx.get("provenance") or {}).get("export_verification") or []
    if not ver:
        return "FAIL", "no export read-back record"
    bad = [c["name"] for c in ver if c.get("outcome") == "failed"]
    return ("PASS", f"{len(ver)} read-back checks, none failed") if not bad else ("FAIL", "failed: " + ", ".join(bad))


def _releasable(ctx):
    p = ctx["plan"]
    return ("PASS", p.get("gate") or "releasable") if p.get("approvable") else ("FAIL", p.get("gate") or "not releasable")


def _sample_complete(ctx):
    st = ctx["measurements"]["sample_state"].get(ctx.get("sample") or "")
    if not st:
        return "FAIL", f"no measurements recorded for sample {ctx.get('sample')!r}"
    if st["missing"] or st["not_evaluable"]:
        return "FAIL", (f"{st['measured']}/{st['required']} measured; missing {', '.join(st['missing'][:8])}"
                        + (f"; not judgeable {', '.join(st['not_evaluable'][:8])}" if st["not_evaluable"] else ""))
    return "PASS", f"all {st['required']} characteristics measured and judged"


def _sample_passes(ctx):
    st = ctx["measurements"]["sample_state"].get(ctx.get("sample") or "")
    if not st:
        return "FAIL", "no measurements for this sample"
    return ("PASS", "no characteristic failed") if not st["fails"] else ("FAIL", f"failed: {', '.join(st['fails'])}")


def _instruments(ctx):
    s = ctx.get("sample")
    entries = [e for e in ctx["log"] if e.get("sample") == s and e.get("value") is not None]
    if not entries:
        return "FAIL", "no measurements for this sample"
    bad = []
    for e in entries:
        ir = e.get("instrument_record")
        if not ir:
            bad.append(f"{e['cid']} (no registered instrument)")
        elif ir.get("calibration_due") and ir["calibration_due"] < e["at"][:10]:
            bad.append(f"{e['cid']} ({ir['id']} calibration due {ir['calibration_due']})")
    return ("PASS", f"{len(entries)} values, each with a calibrated, registered instrument") if not bad else \
        ("FAIL", "; ".join(bad[:8]))


def _first_off(ctx):
    s = ctx.get("sample")
    n = sum(1 for e in ctx["log"] if e.get("sample") == s and e.get("value") is not None)
    return ("PASS", f"{n} value(s) recorded for {s}") if n else ("FAIL", f"nothing recorded for {s!r} yet")


TEMPLATES: dict[str, dict] = {
    "drawing_review": {
        "title": "Drawing review before inspection planning",
        "purpose": "Confirms the drawing states what an inspection plan needs before anyone measures to it.",
        "per_sample": False,
        "items": [
            ("released_revision", "manual", "The drawing is the released revision ordered for this job."),
            ("units", "auto", "Units are stated by the drawing or declared for it.", _units),
            ("revision", "auto", "The revision is stated by the drawing, not defaulted.", _revision),
            ("title_block", "auto", "The title block identifies the part (drawing number or part name).", _title_block),
            ("limits", "auto", "Every characteristic has limits (stated, or from a declared general tolerance).", _all_limits),
            ("interpreted", "auto", "Every dimension-like annotation is interpreted or dismissed with a reason.", _interpreted),
            ("no_blocking", "auto", "No blocking finding is open.", _no_blocking),
            ("ballooning", "auto", "Every characteristic is ballooned; balloons do not overlap, cover the drawing or cross.", _ballooning),
            ("exports", "auto", "Report and workbook were re-opened and match the plan.", _exports),
            ("notes", "manual", "Notes and specifications (material, finish, heat treatment, standards) are reviewed and understood."),
        ],
    },
    "fai_readiness": {
        "title": "First article inspection readiness (AS9102 structure)",
        "purpose": "Before a first-article report is issued for one serial number.",
        "per_sample": True,
        "items": [
            ("plan_releasable", "auto", "The inspection plan is releasable.", _releasable),
            ("measured", "auto", "Every characteristic is measured and judged for this serial.", _sample_complete),
            ("passes", "auto", "No characteristic failed for this serial (or each failure has a nonconformance number).", _sample_passes),
            ("instruments", "auto", "Every value was taken with a registered instrument in calibration on the day.", _instruments),
            ("material", "manual", "Material certificate checked against the drawing's material callout (Form 2)."),
            ("special_processes", "manual", "Special-process certificates (heat treatment, plating, NDT) checked, or N/A (Form 2)."),
            ("functional", "manual", "Functional tests required by the drawing or order done, or N/A (Form 2)."),
            ("identity", "manual", "Part number, serial number and FAI report number recorded (Form 1)."),
        ],
    },
    "setup_first_off": {
        "title": "Machine setup and first-off verification",
        "purpose": "Before production continues after a setup.",
        "per_sample": True,
        "items": [
            ("program_revision", "manual", "The NC program is the revision released for this drawing revision."),
            ("work_offsets", "manual", "Work offsets set and verified against the fixture datum."),
            ("tool_offsets", "manual", "Tool lengths and wear offsets verified; the tool list matches the setup sheet."),
            ("fixturing", "manual", "Fixture and clamping checked; the part is located on the drawing's datums."),
            ("first_off_measured", "auto", "The first-off part has measurements recorded.", _first_off),
            ("first_off_passes", "auto", "The first-off part has no failed characteristic.", _sample_passes),
        ],
    },
}


def log_path(plan_path: Path, template: str, sample: str | None) -> Path:
    stem = plan_path.name[:-len(".plan.json")]
    tail = f"_{re.sub(r'[^\w.-]', '_', sample)[:40]}" if sample else ""
    return plan_path.with_name(f"{stem}.checklist_{template}{tail}.jsonl")


def _read(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def evaluate(template: str, ctx: dict, decisions: list[dict]) -> dict:
    t = TEMPLATES[template]
    latest = {}
    sign = None
    after_sign = False                 # file order is the true sequence (timestamps are per second)
    for d in decisions:
        if d.get("item") == "__sign__":
            sign, after_sign = d, False
        else:
            latest[d["item"]] = d
            after_sign = sign is not None
    items = []
    for it in t["items"]:
        iid, kind, text = it[0], it[1], it[2]
        auto = None
        if kind == "auto":
            st, ev = it[3](ctx)
            auto = {"state": st, "evidence": ev}
        items.append({"id": iid, "kind": kind, "text": text, "auto": auto, "decision": latest.get(iid)})
    decided = [i for i in items if i["decision"]]
    failed = [i["id"] for i in items if i["decision"] and i["decision"]["result"] == "FAIL"]
    complete = len(decided) == len(items) and not failed
    # a signature is only valid for the decisions it was made on
    if sign and after_sign:
        sign = {**sign, "superseded": True}
    return {"template": template, "title": t["title"], "purpose": t["purpose"], "per_sample": t["per_sample"],
            "sample": ctx.get("sample"), "items": items, "decided": len(decided), "total": len(items),
            "failed": failed, "complete": complete,
            "signed": sign if sign and not sign.get("superseded") else None,
            "signature_superseded": bool(sign and sign.get("superseded"))}


def decide(plan_path: Path, template: str, ctx: dict, body: dict) -> dict:
    """Record one decision (item) or the sign-off (item '__sign__'). Raises ValueError to refuse."""
    if template not in TEMPLATES:
        raise ValueError(f"unknown checklist {template!r}")
    t = TEMPLATES[template]
    sample = ctx.get("sample")
    if t["per_sample"] and not sample:
        raise ValueError("this checklist is per serial number: give the sample")
    by = str(body.get("by", "")).strip()
    if not _NAME.fullmatch(by):
        raise ValueError("your name is required (letters, digits, space . ' - ; up to 64)")
    p = log_path(plan_path, template, sample if t["per_sample"] else None)
    decisions = _read(p)
    item = str(body.get("item", ""))
    note = str(body.get("note") or "").strip()[:500]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if item == "__sign__":
        state = evaluate(template, ctx, decisions)
        if not state["complete"]:
            raise ValueError(f"cannot sign: {state['decided']}/{state['total']} decided"
                             + (f", failed: {', '.join(state['failed'])}" if state["failed"] else ""))
        entry = {"at": now, "by": by, "item": "__sign__", "result": "SIGNED", "note": note}
    else:
        spec = next((i for i in t["items"] if i[0] == item), None)
        if spec is None:
            raise ValueError(f"unknown item {item!r}")
        result = str(body.get("result", "")).upper()
        if result not in ("PASS", "FAIL", "NA"):
            raise ValueError("result must be PASS, FAIL or NA")
        if result in ("FAIL", "NA") and not note:
            raise ValueError(f"{result} needs a note saying why")
        evidence = None
        if spec[1] == "auto":
            st, ev = spec[3](ctx)
            evidence = {"state": st, "evidence": ev}
            if result == "PASS" and st == "FAIL":
                raise ValueError(f"cannot pass: ASTraCAD's evidence for this item fails ({ev})")
        entry = {"at": now, "by": by, "item": item, "result": result, "note": note, "evidence_seen": evidence}
    with _lock:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return evaluate(template, ctx, decisions + [entry])


def load(plan_path: Path, template: str, ctx: dict) -> dict:
    if template not in TEMPLATES:
        raise ValueError(f"unknown checklist {template!r}")
    per = TEMPLATES[template]["per_sample"]
    if per and not ctx.get("sample"):
        return {"template": template, "title": TEMPLATES[template]["title"], "per_sample": True,
                "needs_sample": True, "items": [], "decided": 0, "total": len(TEMPLATES[template]["items"])}
    return evaluate(template, ctx, _read(log_path(plan_path, template, ctx.get("sample") if per else None)))


def write_xlsx(state: dict, history: list[dict], out: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Checklist"
    ws.append([state["title"]])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Sample", state.get("sample") or "-"])
    ws.append(["Status", "SIGNED by " + state["signed"]["by"] + " " + state["signed"]["at"] if state.get("signed")
               else ("complete, not signed" if state["complete"] else f"{state['decided']}/{state['total']} decided")])
    ws.append([])
    ws.append(["Item", "Kind", "Requirement", "ASTraCAD evidence", "Decision", "By", "At", "Note"])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for i in state["items"]:
        d = i["decision"] or {}
        ev = f"{i['auto']['state']}: {i['auto']['evidence']}" if i["auto"] else ""
        ws.append([i["id"], i["kind"], i["text"], ev, d.get("result", ""), d.get("by", ""), d.get("at", ""), d.get("note", "")])
    h = wb.create_sheet("History")
    h.append(["At", "By", "Item", "Result", "Note", "Evidence seen"])
    for e in history:
        h.append([e["at"], e["by"], e["item"], e["result"], e.get("note", ""),
                  json.dumps(e.get("evidence_seen"), ensure_ascii=False) if e.get("evidence_seen") else ""])
    out = Path(out)
    wb.save(out)
    return out
