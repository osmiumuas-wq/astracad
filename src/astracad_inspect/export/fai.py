"""First Article Inspection workbook in the AS9102 three-form structure, from a finished plan.

Form 1 (part number accountability), Form 2 (product accountability: materials, special processes,
functional tests) and Form 3 (characteristic accountability). Only what the plan knows is filled:
characteristic numbers, zones, requirements and limits. Results, serial numbers, organisation
data and Form 2 contents are left blank for the organisation performing the FAI; nothing is
invented. AS9102 is an SAE International standard: confirm the revision your customer requires
(the field structure here follows the published forms) and use their template when mandated.
"""
from __future__ import annotations

import json
from pathlib import Path

TO_COMPLETE = "<to be completed by the organisation>"


def _requirement(row: dict) -> str:
    req = row.get("callout") or row.get("raw_callout") or ""
    if row.get("minimum") is not None and row.get("maximum") is not None:
        req += f"  [limits {row['minimum']:g} .. {row['maximum']:g} {row.get('unit', '')}]"
    return req


def write_fai(plan_json: str | Path, out_xlsx: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    plan = json.loads(Path(plan_json).read_text(encoding="utf-8"))
    rows = plan.get("rows") or []
    zones = {c["cid"]: (c.get("source") or {}).get("zone", "") for c in plan.get("characteristics") or []}
    tb = plan.get("title_block") or {}
    wb = Workbook()

    f1 = wb.active
    f1.title = "Form 1"
    f1.append(["AS9102 Form 1 - Part Number Accountability (structure per the published form)"])
    for label, value in [
        ("1. Part number", tb.get("drawing_number") or TO_COMPLETE),
        ("2. Part name", tb.get("part_name") or tb.get("title") or TO_COMPLETE),
        ("3. Serial number", TO_COMPLETE),
        ("4. FAI report number", TO_COMPLETE),
        ("5. Part revision level", plan.get("revision") or TO_COMPLETE),
        ("6. Drawing number", tb.get("drawing_number") or TO_COMPLETE),
        ("7. Drawing revision level", f"{plan.get('revision')} ({plan.get('revision_source')})"),
        ("8. Additional changes", TO_COMPLETE),
        ("9. Manufacturing process reference", TO_COMPLETE),
        ("10. Organization name", TO_COMPLETE),
        ("11. Supplier code", TO_COMPLETE),
        ("12. P.O. number", TO_COMPLETE),
        ("13. Detail / assembly FAI", TO_COMPLETE),
        ("14. Full / partial FAI", TO_COMPLETE),
        ("Source drawing", plan.get("source_document")),
        ("Source SHA-256", plan.get("source_sha256")),
        ("Plan gate", plan.get("gate")),
    ]:
        f1.append([label, value])
    f1.column_dimensions["A"].width, f1.column_dimensions["B"].width = 36, 90

    f2 = wb.create_sheet("Form 2")
    f2.append(["AS9102 Form 2 - Product Accountability (materials, special processes, functional tests)"])
    f2.append(["Material or process name", "Specification number", "Code", "Special process supplier code",
               "Customer approval verification", "Certificate of conformance number"])
    f2.append([TO_COMPLETE] + [""] * 5)

    f3 = wb.create_sheet("Form 3")
    f3.append(["AS9102 Form 3 - Characteristic Accountability, Verification and Compatibility Evaluation"])
    head = ["5. Char. no.", "6. Reference location", "7. Characteristic designator", "8. Requirement",
            "9. Results", "10. Designed / qualified tooling", "11. Nonconformance number",
            "Characteristic ID", "Source entity", "Provenance / status"]
    f3.append(head)
    for c in f3[2]:
        c.font = Font(bold=True)
        c.alignment = Alignment(wrap_text=True)
    for r in rows:
        zone = zones.get(r.get("cid"), "")
        loc = f"sheet {r.get('page') or 1}" + (f", zone {zone}" if zone else "")
        f3.append([r.get("balloon_no"), loc, "", _requirement(r), "", "", "",
                   r.get("cid"), r.get("entity_handle"), f"{r.get('provenance')} / {r.get('status')}"])
    for col, w in zip("ABCDEFGHIJ", (10, 22, 14, 60, 18, 18, 16, 12, 18, 22)):
        f3.column_dimensions[col].width = w
    out = Path(out_xlsx)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out


def verify_fai(plan_json: str | Path, xlsx: str | Path) -> list[tuple[str, bool, str]]:
    """Re-open the workbook: one Form 3 row per plan row, same balloon numbers and requirements."""
    from openpyxl import load_workbook
    plan = json.loads(Path(plan_json).read_text(encoding="utf-8"))
    ws = load_workbook(xlsx, read_only=True)["Form 3"]
    got = [r for r in ws.iter_rows(min_row=3, values_only=True) if any(v is not None for v in r)]
    want = plan.get("rows") or []
    checks = [("form3_row_count", len(got) == len(want), f"{len(got)}/{len(want)}")]
    bad = [w.get("cid") for g, w in zip(got, want)
           if g[0] != w.get("balloon_no") or g[3] != _requirement(w)]
    checks.append(("form3_rows_match_plan", not bad, "all match" if not bad else f"differ: {bad}"))
    return checks
