"""Cost estimate deliverables: JSON (complete record) and XLSX (lines, inputs, warnings), each
re-opened after writing and compared with the computed estimate."""
from __future__ import annotations

import json
from pathlib import Path

from .costing import Estimate


def write(est: Estimate, out_dir: str | Path, name: str) -> dict[str, Path]:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    js = out / f"{name}.cost.json"
    js.write_text(json.dumps(est.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    wb = Workbook()
    ws = wb.active
    ws.title = "Lines"
    ws.append(["Line", "Value", "Unit", "Formula", "Inputs (value | source | as of)"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for ln in est.lines:
        parts = []
        for k, v in ln.inputs.items():
            if isinstance(v, dict) and "source" in v:
                parts.append(f"{k}={v['value']:g} {v['unit']} | {v['source']} | {v['as_of']}")
            else:
                parts.append(f"{k}={v}")
        ws.append([ln.name, ln.value, ln.unit, ln.formula, "; ".join(parts)])
    for col, w in zip("ABCDE", (18, 16, 10, 48, 90)):
        ws.column_dimensions[col].width = w
    wi = wb.create_sheet("Inputs")
    wi.append(["Input", "Value", "Unit", "Source", "As of"])
    for k, v in est.inputs.items():
        if isinstance(v, dict):
            wi.append([k, v["value"], v["unit"], v["source"], v["as_of"]])
        else:
            wi.append([k, v, "", "job", ""])
    wi.append(["part volume", est.geometry.volume_mm3, "mm^3", est.geometry.volume_source, ""])
    wi.append(["part size", " x ".join(f"{s:g}" for s in est.geometry.size_mm), "mm",
               est.geometry.size_source, ""])
    ww = wb.create_sheet("Warnings")
    ww.append(["Warning"])
    for w in est.warnings or ["none"]:
        ww.append([w])
    wn = wb.create_sheet("Scope")
    wn.append([est.scope_note])
    xl = out / f"{name}.cost.xlsx"
    wb.save(xl)
    return {"json": js, "xlsx": xl}


def verify(est: Estimate, files: dict[str, Path]) -> list[tuple[str, bool, str]]:
    """Re-open both files and compare every line value with the computed estimate."""
    from openpyxl import load_workbook
    checks = []
    back = json.loads(files["json"].read_text(encoding="utf-8"))
    checks.append(("json_cost_per_part", back["cost_per_part"] == est.cost_per_part,
                   f"{back['cost_per_part']} vs {est.cost_per_part}"))
    ws = load_workbook(files["xlsx"], read_only=True)["Lines"]
    rows = {r[0]: r[1] for r in ws.iter_rows(min_row=2, values_only=True)}
    bad = [ln.name for ln in est.lines if rows.get(ln.name) is None
           or abs(rows[ln.name] - ln.value) > 1e-9 * max(1.0, abs(ln.value))]
    checks.append(("xlsx_every_line_value", not bad, "all match" if not bad else f"differ: {bad}"))
    checks.append(("xlsx_line_count", len(rows) == len(est.lines), f"{len(rows)}/{len(est.lines)}"))
    return checks
