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
import math
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


AUTO_CHECKS = {
    "units": (_units, "Units are stated by the drawing or declared for it"),
    "revision": (_revision, "The revision is stated by the drawing, not defaulted"),
    "title_block": (_title_block, "The title block identifies the part"),
    "limits": (_all_limits, "Every characteristic has limits"),
    "interpreted": (_interpreted, "Every dimension-like annotation is interpreted or dismissed"),
    "no_blocking": (_no_blocking, "No blocking finding is open"),
    "ballooning": (_ballooning, "Ballooning complete, no overlaps, nothing on the drawing, no crossings"),
    "exports": (_exports, "Report and workbook re-opened and match the plan"),
    "plan_releasable": (_releasable, "The inspection plan is releasable"),
    "sample_measured": (_sample_complete, "Every characteristic measured and judged for this serial"),
    "sample_passes": (_sample_passes, "No characteristic failed for this serial"),
    "instruments": (_instruments, "Every value taken with a registered instrument in calibration"),
    "first_off": (_first_off, "Measurements recorded for this serial"),
}
_FN_NAME = {fn: name for name, (fn, _) in AUTO_CHECKS.items()}
ITEM_TYPES = ("check", "numeric", "text", "auto")
_ID = re.compile(r"[a-z0-9][a-z0-9_-]{1,40}")


def _builtin(tid: str) -> dict:
    t = TEMPLATES[tid]
    items = [{"id": it[0], "type": "auto" if it[1] == "auto" else "check", "text": it[2],
              **({"auto": _FN_NAME[it[3]]} if it[1] == "auto" else {})} for it in t["items"]]
    return {"id": tid, "title": t["title"], "purpose": t["purpose"], "per_sample": t["per_sample"],
            "items": items, "version": 0, "builtin": True}


def validate_template(body: dict) -> dict:
    """A custom template as stored. Raises ValueError naming the first problem."""
    tid = str(body.get("id", "")).strip().lower()
    if not _ID.fullmatch(tid):
        raise ValueError("template id: lower-case letters, digits, - or _ (2-41), starting with a letter or digit")
    if tid in TEMPLATES:
        raise ValueError(f"{tid!r} is a built-in template; choose another id (or duplicate it under a new id)")
    title = str(body.get("title", "")).strip()[:120]
    if not title:
        raise ValueError("a title is required")
    items_in = body.get("items") or []
    if not isinstance(items_in, list) or not items_in:
        raise ValueError("a checklist needs at least one item")
    if len(items_in) > 200:
        raise ValueError("at most 200 items")
    items, seen = [], set()
    for k, it in enumerate(items_in, start=1):
        if not isinstance(it, dict):
            raise ValueError(f"item {k}: not an item")
        iid = str(it.get("id") or f"item{k}").strip().lower()
        if not _ID.fullmatch(iid):
            raise ValueError(f"item {k}: id {iid!r} is invalid (lower-case letters, digits, - or _, 2-41 characters)")
        if iid in seen:
            raise ValueError(f"item {k}: id {iid!r} is repeated")
        seen.add(iid)
        typ = str(it.get("type", "check"))
        if typ not in ITEM_TYPES:
            raise ValueError(f"item {k}: type must be one of {', '.join(ITEM_TYPES)}")
        text = str(it.get("text", "")).strip()[:400]
        out = {"id": iid, "type": typ, "text": text, "guidance": str(it.get("guidance") or "").strip()[:400]}
        if typ == "auto":
            a = str(it.get("auto", ""))
            if a not in AUTO_CHECKS:
                raise ValueError(f"item {k}: automatic check must be one of {', '.join(AUTO_CHECKS)}")
            out["auto"] = a
            out["text"] = text or AUTO_CHECKS[a][1]
        if not out["text"]:
            raise ValueError(f"item {k}: the requirement text is empty")
        if typ == "numeric":
            unit = str(it.get("unit") or "").strip()[:16]
            lo, hi = it.get("min"), it.get("max")
            lo = None if lo in (None, "") else float(lo)
            hi = None if hi in (None, "") else float(hi)
            for v, nm in ((lo, "minimum"), (hi, "maximum")):
                if v is not None and not math.isfinite(v):
                    raise ValueError(f"item {k}: {nm} must be a finite number")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"item {k}: minimum {lo:g} is above maximum {hi:g}")
            out.update(unit=unit, min=lo, max=hi)
        items.append(out)
    return {"id": tid, "title": title, "purpose": str(body.get("purpose") or "").strip()[:400],
            "per_sample": bool(body.get("per_sample")), "items": items}


class TemplateStore:
    """Custom templates, one JSON per template holding every version (a version is never edited)."""

    def __init__(self, root: str | Path):
        self.dir = Path(root) / "checklist_templates"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _p(self, tid: str) -> Path:
        if not _ID.fullmatch(str(tid or "")):
            raise PermissionError("invalid template id")
        return self.dir / f"{tid}.json"

    def save(self, body: dict) -> dict:
        by = str(body.get("by", "")).strip()
        if not _NAME.fullmatch(by):
            raise ValueError("your name is required to save a template")
        t = validate_template(body)
        with _lock:
            p = self._p(t["id"])
            doc = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"id": t["id"], "versions": []}
            last = doc["versions"][-1] if doc["versions"] else None
            core = {k: t[k] for k in ("title", "purpose", "per_sample", "items")}
            if last and {k: last[k] for k in core} == core:
                return self.get(t["id"])
            ver = {"version": len(doc["versions"]) + 1, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "by": by, **core}
            doc["versions"].append(ver)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        return self.get(t["id"])

    def get(self, tid: str, version: int | None = None) -> dict:
        p = self._p(tid)
        if not p.exists():
            raise FileNotFoundError(f"no checklist template {tid!r}")
        doc = json.loads(p.read_text(encoding="utf-8"))
        vs = doc["versions"]
        v = vs[-1] if version is None else next((x for x in vs if x["version"] == int(version)), None)
        if v is None:
            raise FileNotFoundError(f"{tid} has no version {version}")
        return {"id": tid, "builtin": False, "versions": [{"version": x["version"], "at": x["at"], "by": x["by"]} for x in vs], **v}

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(self.get(p.stem))
            except (OSError, ValueError, FileNotFoundError, PermissionError):
                continue
        return out


def resolve(tid: str, store: TemplateStore | None, version: int | None = None) -> dict:
    if tid in TEMPLATES:
        return _builtin(tid)
    if store is None:
        raise ValueError(f"unknown checklist {tid!r}")
    try:
        return store.get(tid, version)
    except (FileNotFoundError, PermissionError):
        raise ValueError(f"unknown checklist {tid!r}") from None


def log_path(plan_path: Path, template: str, sample: str | None) -> Path:
    stem = plan_path.name[:-len(".plan.json")]
    tail = f"_{re.sub(r'[^\w.-]', '_', sample)[:40]}" if sample else ""
    return plan_path.with_name(f"{stem}.checklist_{template}{tail}.jsonl")


def _read(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def _pinned(tid: str, store, decisions: list[dict]) -> dict:
    """The version an instance started on stays its version: a later edit never rewrites answers."""
    first = next((d for d in decisions if d.get("template_version") is not None), None)
    return resolve(tid, store, first["template_version"] if first and first["template_version"] else None)


def _judge(item: dict, value: float) -> tuple[str, str]:
    lo, hi = item.get("min"), item.get("max")
    u = item.get("unit") or ""
    if lo is None and hi is None:
        return "RECORDED", f"{value:g} {u} recorded (no limits set)"
    tol = 1e-9 * max(1.0, abs(lo or 0), abs(hi or 0))
    band = (f"{lo:g} .. {hi:g}" if lo is not None and hi is not None else
            f"at least {lo:g}" if lo is not None else f"at most {hi:g}")
    if lo is not None and value < lo - tol:
        return "FAIL", f"{value:g} {u} is below the minimum {lo:g} (required {band} {u})".replace("  ", " ")
    if hi is not None and value > hi + tol:
        return "FAIL", f"{value:g} {u} is above the maximum {hi:g} (required {band} {u})".replace("  ", " ")
    return "PASS", f"{value:g} {u} meets {band} {u}".replace("  ", " ")


def evaluate(t: dict, ctx: dict, decisions: list[dict]) -> dict:
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
        auto = None
        if it["type"] == "auto":
            st, ev = AUTO_CHECKS[it["auto"]][0](ctx)
            auto = {"state": st, "evidence": ev}
        items.append({**it, "kind": "auto" if it["type"] == "auto" else "manual", "auto": auto,
                      "decision": latest.get(it["id"])})
    decided = [i for i in items if i["decision"]]
    failed = [i["id"] for i in items if i["decision"] and i["decision"]["result"] == "FAIL"]
    complete = len(decided) == len(items) and not failed
    if sign and after_sign:
        sign = {**sign, "superseded": True}
    return {"template": t["id"], "version": t.get("version", 0), "builtin": t.get("builtin", False),
            "title": t["title"], "purpose": t.get("purpose", ""), "per_sample": t["per_sample"],
            "sample": ctx.get("sample"), "items": items, "decided": len(decided), "total": len(items),
            "failed": failed, "complete": complete,
            "signed": sign if sign and not sign.get("superseded") else None,
            "signature_superseded": bool(sign and sign.get("superseded"))}


def decide(plan_path: Path, template: str, ctx: dict, body: dict, store: TemplateStore | None = None) -> dict:
    """Record one decision (item) or the sign-off (item '__sign__'). Raises ValueError to refuse."""
    head = resolve(template, store)
    sample = ctx.get("sample")
    if head["per_sample"] and not sample:
        raise ValueError("this checklist is per serial number: give the sample")
    by = str(body.get("by", "")).strip()
    if not _NAME.fullmatch(by):
        raise ValueError("your name is required (letters, digits, space . ' - ; up to 64)")
    p = log_path(plan_path, template, sample if head["per_sample"] else None)
    decisions = _read(p)
    t = _pinned(template, store, decisions)
    item = str(body.get("item", ""))
    note = str(body.get("note") or "").strip()[:500]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if item == "__sign__":
        state = evaluate(t, ctx, decisions)
        if not state["complete"]:
            raise ValueError(f"cannot sign: {state['decided']}/{state['total']} decided"
                             + (f", failed: {', '.join(state['failed'])}" if state["failed"] else ""))
        entry = {"at": now, "by": by, "item": "__sign__", "result": "SIGNED", "note": note,
                 "template_version": t.get("version", 0)}
    else:
        spec = next((i for i in t["items"] if i["id"] == item), None)
        if spec is None:
            raise ValueError(f"unknown item {item!r}")
        result = str(body.get("result", "")).upper()
        entry = {"at": now, "by": by, "item": item, "note": note, "template_version": t.get("version", 0)}
        if result == "NA":
            if not note:
                raise ValueError("NA needs a note saying why")
            entry["result"] = "NA"
        elif spec["type"] == "numeric":
            try:
                x = float(str(body.get("value", "")).strip().replace(",", "."))
            except ValueError:
                raise ValueError("enter the measured value as a number") from None
            if not math.isfinite(x):
                raise ValueError("the value must be finite")
            res, why = _judge(spec, x)
            entry.update(result=res, value=x, unit=spec.get("unit") or "", judged=why)
        elif spec["type"] == "text":
            txt = str(body.get("text") or "").strip()[:1000]
            if not txt:
                raise ValueError("enter the answer")
            entry.update(result="RECORDED", text=txt)
        else:
            if result not in ("PASS", "FAIL"):
                raise ValueError("result must be PASS, FAIL or NA")
            if result == "FAIL" and not note:
                raise ValueError("FAIL needs a note saying why")
            entry["result"] = result
            if spec["type"] == "auto":
                st, ev = AUTO_CHECKS[spec["auto"]][0](ctx)
                entry["evidence_seen"] = {"state": st, "evidence": ev}
                if result == "PASS" and st == "FAIL":
                    raise ValueError(f"cannot pass: ASTraCAD's evidence for this item fails ({ev})")
    with _lock:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return evaluate(t, ctx, decisions + [entry])


def load(plan_path: Path, template: str, ctx: dict, store: TemplateStore | None = None) -> dict:
    head = resolve(template, store)
    per = head["per_sample"]
    if per and not ctx.get("sample"):
        return {"template": template, "title": head["title"], "per_sample": True, "needs_sample": True,
                "items": [], "decided": 0, "total": len(head["items"])}
    decisions = _read(log_path(plan_path, template, ctx.get("sample") if per else None))
    return evaluate(_pinned(template, store, decisions), ctx, decisions)


def write_xlsx(state: dict, history: list[dict], out: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Checklist"
    ws.append([state["title"]])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Sample", state.get("sample") or "-"])
    ws.append(["Template", f"{state['template']} v{state.get('version', 0)}" + (" (built-in)" if state.get("builtin") else "")])
    ws.append(["Status", "SIGNED by " + state["signed"]["by"] + " " + state["signed"]["at"] if state.get("signed")
               else ("complete, not signed" if state["complete"] else f"{state['decided']}/{state['total']} decided")])
    ws.append([])
    ws.append(["Item", "Kind", "Requirement", "ASTraCAD evidence", "Decision", "Value / answer", "By", "At", "Note"])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for i in state["items"]:
        d = i["decision"] or {}
        ev = f"{i['auto']['state']}: {i['auto']['evidence']}" if i["auto"] else ""
        val = (f"{d['value']:g} {d.get('unit', '')}".strip() if d.get("value") is not None else d.get("text", ""))
        ws.append([i["id"], i["kind"], i["text"], ev, d.get("result", ""), val, d.get("by", ""), d.get("at", ""), d.get("note", "")])
    h = wb.create_sheet("History")
    h.append(["At", "By", "Item", "Result", "Note", "Evidence seen"])
    for e in history:
        h.append([e["at"], e["by"], e["item"], e["result"], e.get("note", ""),
                  json.dumps(e.get("evidence_seen"), ensure_ascii=False) if e.get("evidence_seen") else ""])
    out = Path(out)
    wb.save(out)
    return out
