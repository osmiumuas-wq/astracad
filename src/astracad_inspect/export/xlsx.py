"""XLSX inspection table.

Spreadsheet cells are typed, so an unknown limit is left empty rather than written as zero or as
a dash: a zero in a limit column is a claim, and the drawing did not make one.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table as XLTable, TableStyleInfo

from ..provenance import ProvenanceRecord
from ..plan import InspectionPlan

HEAD = Font(bold=True)
BLOCK_FILL = PatternFill("solid", fgColor="FDECEF")
EDIT_FILL = PatternFill("solid", fgColor="FFFBE8")
WRAP = Alignment(wrap_text=True, vertical="top")


def _sheet(ws, headers: list[str], rows: list[list], widths: list[int], *, name: str,
           fills: list[str | None] | None = None) -> None:
    ws.append(headers)
    for c in ws[1]:
        c.font = HEAD
        c.alignment = WRAP
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = WRAP
    if fills:
        for i, kind in enumerate(fills):
            if kind:
                for cell in ws[i + 2]:
                    cell.fill = BLOCK_FILL if kind == "block" else EDIT_FILL
    ws.freeze_panes = "A2"
    ref = f"A1:{get_column_letter(len(headers))}{max(len(rows) + 1, 2)}"
    tbl = XLTable(displayName=name.replace(" ", ""), ref=ref)
    tbl.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=True)
    ws.add_table(tbl)


def write_workbook(plan: InspectionPlan, record: ProvenanceRecord, out_xlsx: str | Path) -> Path:
    out = Path(out_xlsx)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    ws = wb.active
    ws.title = "Inspection table"
    rows, fills = [], []
    for r in plan.rows:
        rows.append([
            r.balloon_no, r.cid, r.kind, r.raw_callout, r.callout, r.count, r.unit,
            r.nominal, r.tolerance_text, r.minimum, r.maximum,
            None if r.minimum is None else r.maximum - r.minimum,
            r.provenance, r.status, "yes" if r.blocking else "",
            "; ".join(r.issues), r.method, r.method_basis,
            r.source_document, r.entity_type, r.entity_handle, r.layer, r.page,
            r.source_sha256,
        ])
        fills.append("block" if r.blocking else ("edit" if r.provenance in
                   ("user_specified", "human_approved") else None))
    _sheet(ws, [
        "Balloon", "ID", "Type", "Callout (raw source text)", "Callout (normalised)", "Qty",
        "Unit", "Nominal", "Tolerance (as interpreted)", "Min limit", "Max limit",
        "Tolerance band", "Provenance state", "Review status", "Blocking", "Findings",
        "Measurement method", "Method basis", "Source document", "Source entity",
        "Entity handle", "Layer", "Page", "Source SHA-256",
    ], rows, [8, 9, 10, 22, 20, 5, 6, 10, 20, 10, 10, 12, 14, 13, 9, 30, 22, 34, 26, 12, 11,
              8, 6, 22], name="Inspection table", fills=fills)

    bl = wb.create_sheet("Balloons")
    _sheet(bl, ["Balloon", "ID", "Centre X", "Centre Y", "Radius", "Anchor X", "Anchor Y",
                "Leader"],
           [[b.number, b.cid, b.centre[0], b.centre[1], b.radius, b.anchor[0], b.anchor[1],
             " -> ".join(f"({p[0]:g}, {p[1]:g})" for p in b.leader)]
            for b in plan.layout.balloons],
           [8, 9, 11, 11, 8, 10, 10, 30], name="Balloons")

    fd = wb.create_sheet("Checks and findings")
    check_rows = [[c.name, c.outcome.value, c.expected, c.actual, c.why or "",
                   "" if c.delta is None else c.delta] for c in plan.checks]
    _sheet(fd, ["Check / finding", "Kind", "Outcome", "Expected", "Actual", "Why", "Delta"],
           [["check", "", *r] for r in check_rows] +
           [["finding", "", "blocking", "-", "-", f"{i.code}: {i.message}", ""]
            for i in plan.issues] +
           [["escalated annotation", "", "needs_review", "-", "-",
             f"{u.text!r} on layer {u.layer}: {u.reason}", ""]
            for u in plan.uninterpreted] +
           [["note", "", "informational", "-", "-", n, ""] for n in plan.notes],
           [20, 34, 12, 30, 30, 60, 10], name="Checks and findings")

    ex = wb.create_sheet("Feature support")
    _sheet(ex, ["Capability", "Status in this build"],
           [[k, v] for k, v in sorted(plan.feature_support.items())],
           [34, 70], name="Feature support")

    pv = wb.create_sheet("Provenance")
    _sheet(pv, ["Item", "Value"],
           [["Generated (UTC)", record.created_utc],
            ["Engine", f"{record.engine} {record.engine_version}"],
            ["Route", record.route],
            ["Source document", plan.source_document],
            ["Source SHA-256", plan.source_sha256],
            ["Revision", plan.revision],
            ["Unit basis", plan.unit],
            *[[f"tool: {k}", v] for k, v in record.tools.items()],
            *[[f"output: {o['path']}", f"{o['bytes']} B sha256={o['sha256']}"]
              for o in record.outputs],
            *[["limitation", lim] for lim in record.limitations],
            ["Structured source of truth",
             "the plan JSON beside this workbook; tolerance data is not held only in exports"]],
           [30, 100], name="Provenance")

    wb.save(str(out))
    return out
