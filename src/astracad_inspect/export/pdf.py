"""PDF inspection report.

The report is a rendering of the plan, never a second source of truth: every number in it comes
from the plan object, and the machine-readable JSON alongside it carries the same records so the
tolerance data does not exist only inside a PDF.

The gate statement appears on the first page. A report that could be read as complete while
characteristics are still unresolved would be the failure mode this whole subsystem exists to
avoid, so the release status is not left to the reader to infer.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from ..provenance import ProvenanceRecord
from ..plan import InspectionPlan

RED = colors.HexColor("#b00020")
GREY = colors.HexColor("#666666")
LINE = colors.HexColor("#cfcfcf")

TABLE_COLUMNS = [
    ("Balloon", 13 * mm, "balloon_no"),
    ("ID", 14 * mm, "cid"),
    ("Type", 17 * mm, "kind"),
    ("Callout", 34 * mm, "callout"),
    ("Nominal", 18 * mm, "nominal"),
    ("Tolerance", 30 * mm, "tolerance_text"),
    ("Min", 17 * mm, "minimum"),
    ("Max", 17 * mm, "maximum"),
    ("Qty", 10 * mm, "count"),
    ("Measurement method", 34 * mm, "method"),
    ("Status", 26 * mm, "status"),
    ("Source entity", 24 * mm, "_source"),
]


def _engineering_font() -> str:
    """A face that actually carries the diameter sign. Helvetica does not: it silently substitutes
    another glyph, which puts a wrong symbol on a controlled engineering document without any
    check failing. DejaVuSans ships with matplotlib, which this project already depends on."""
    try:
        import matplotlib
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        path = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf" / "DejaVuSans.ttf"
        if path.exists():
            pdfmetrics.registerFont(TTFont("Eng", str(path)))
            return "Eng"
    except Exception:  # a missing optional font must not stop the report being written
        pass
    return "Helvetica"


ENGINEERING_FONT = _engineering_font()


def _styles():
    base = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=15, leading=18,
                             spaceAfter=4, textColor=colors.HexColor("#111111")),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=11, leading=14,
                             spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontSize=8, leading=10.5,
                               alignment=TA_LEFT),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontSize=6.8, leading=8.6,
                                textColor=GREY),
        "cell": ParagraphStyle("cell", parent=base["BodyText"], fontSize=7, leading=8.6,
                               fontName=ENGINEERING_FONT),
        "gate": ParagraphStyle("gate", parent=base["BodyText"], fontSize=9.5, leading=12.5),
    }


def fmt_number(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _table(data: list[list], widths: list[float], *, header: bool = True) -> Table:
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2") if header else colors.white),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111111")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), ENGINEERING_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return t


def _numbered(title: str, footer_left: str):
    """Two-pass canvas so the footer can say 'page N of M' rather than a truncated guess."""
    from reportlab.pdfgen import canvas as _canvas

    class NumberedCanvas(_canvas.Canvas):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._saved: list[dict] = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total: int):
            w, h = landscape(A4)
            self.setStrokeColor(LINE)
            self.line(12 * mm, h - 14 * mm, w - 12 * mm, h - 14 * mm)
            self.line(12 * mm, 12 * mm, w - 12 * mm, 12 * mm)
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(colors.HexColor("#111111"))
            self.drawString(12 * mm, h - 12 * mm, title)
            self.setFont("Helvetica", 6.5)
            self.setFillColor(GREY)
            self.drawRightString(w - 12 * mm, h - 12 * mm, "ASTraCAD Inspect")
            self.drawString(12 * mm, 8 * mm, footer_left)
            self.drawRightString(w - 12 * mm, 8 * mm,
                                 f"page {self._pageNumber} of {total}")

    return NumberedCanvas


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inspection_table(plan: InspectionPlan, S) -> Table:
    head = [c[0] for c in TABLE_COLUMNS]
    rows = [head]
    for r in plan.rows:
        src = "-" if r.entity_handle else "reviewer-stated"
        if r.entity_handle:
            src = f"{r.entity_type} {r.entity_handle}\nlayer {r.layer}"
        rows.append([
            Paragraph(f"<b>{fmt_number(r.balloon_no)}</b>", S["cell"]),
            r.cid, r.kind, Paragraph(
                _esc(r.callout) + ("" if r.callout == r.raw_callout else
                                   f"<br/><font size=5.6 color='#666666'>source: "
                                   f"{_esc(r.raw_callout)}</font>"), S["cell"]),
            fmt_number(r.nominal), r.tolerance_text, fmt_number(r.minimum), fmt_number(r.maximum), str(r.count),
            Paragraph(r.method + ("" if r.method.startswith("<") else
                                  f"<font size=5.6 color='#666666'><br/>{r.method_basis}</font>"),
                      S["cell"]),
            r.status + (" +blocked" if r.blocking else ""), src,
        ])
    t = _table(rows, [c[1] for c in TABLE_COLUMNS])
    style = [("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#111111"))]
    for i, r in enumerate(plan.rows, start=1):
        if r.blocking:
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fdecef")))
        elif r.provenance in ("user_specified", "human_approved"):
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fffbe8")))
    t.setStyle(TableStyle(style))
    return t


def write_report(plan: InspectionPlan, record: ProvenanceRecord, drawing_png: str | Path,
                 out_pdf: str | Path) -> Path:
    S = _styles()
    out = Path(out_pdf)
    out.parent.mkdir(parents=True, exist_ok=True)

    tb = plan.title_block
    doc_no = tb.get("drawing_number") or Path(plan.source_document).stem
    title = f"Inspection plan - {doc_no} rev {plan.revision}"
    footer = (f"source {Path(plan.source_document).name}  sha256:{plan.source_sha256[:16]}...  "
              f"generated {record.created_utc}  engine {record.engine} {record.engine_version}")

    doc = SimpleDocTemplate(
        str(out), pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=18 * mm, bottomMargin=15 * mm,
        title=title, author=f"ASTraCAD Inspect {record.engine_version}",
        subject=f"Automated inspection layout for {doc_no}",
        keywords="inspection plan, balloons, GD&T, engineering drawing, provenance",
        creator="astracad_inspect.export.pdf")

    story: list = []
    gate_ok = plan.approvable
    gate_hex = "#0a7d33" if gate_ok else "#b00020"

    # The synthetic-fixture marker is deliberately the first thing on the page: a labelled test
    # drawing must not be able to pass as production output.
    if "synthetic" in Path(plan.source_document).name.lower():
        story.append(Paragraph(
            "<b>SYNTHETIC TEST FIXTURE</b> - generated by tools/make_fixtures.py. "
            "Not a real part, not a real drawing, and not a production deliverable.",
            ParagraphStyle("warn", parent=S["gate"], textColor=RED)))
        story.append(Spacer(1, 3 * mm))

    story += [
        Paragraph(title, S["h1"]),
        Paragraph(
            f"Source drawing <b>{Path(plan.source_document).name}</b> "
            f"(sha256 {plan.source_sha256[:24]}...), unit basis <b>{plan.unit}</b>, "
            f"{plan.coverage.dimensions_in_source} dimension entities read.", S["body"]),
        Spacer(1, 2 * mm),
        Paragraph(f'<font color="{gate_hex}"><b>Release gate:</b> '
                  f'{plan.gate_statement()}</font>', S["gate"]),
        Spacer(1, 3 * mm),
    ]

    cov = plan.coverage
    story += [
        Paragraph("Extraction coverage", S["h2"]),
        _table([
            ["Metric", "Value", "Denominator / method"],
            ["Dimension entities in source", str(cov.dimensions_in_source),
             "entity census of modelspace (DIMENSION + ARC_DIMENSION)"],
            ["Characteristics extracted",
             f"{cov.characteristics_extracted} ({cov.extraction_rate_text})",
             "extracted / dimension entities in the same file"],
            ["Resolved by the parser", f"{cov.resolved}", "callout text fully interpreted"],
            ["Unresolved", f"{cov.unresolved}",
             "present in source, not interpretable without inventing data"],
            ["Blocked", f"{cov.blocking}", "carries a blocking finding or unresolved state"],
            ["Cross-checked against geometry",
             f"{cov.geometry_cross_check_passed}/{cov.geometry_cross_checked}",
             "written callout vs. the dimension's own definition points"],
            ["Balloons placed",
             f"{cov.balloon_assigned} (+{cov.balloon_unplaced} unplaced)",
             "unique number per characteristic, non-overlapping lattice"],
            ["Annotation text escalated", str(cov.annotations_escalated),
             "free text resembling a dimension; no definition points to verify it"],
        ], [58 * mm, 34 * mm, 150 * mm]),
    ]

    if plan.checks:
        story += [Spacer(1, 3 * mm), Paragraph("Plan validation", S["h2"])]
        crows = [["Check", "Outcome", "Expected", "Actual"]]
        for c in plan.checks:
            crows.append([c.name, c.outcome.value,
                          Paragraph(c.expected, S["cell"]),
                          Paragraph(c.actual, S["cell"])])
        story.append(_table(crows, [62 * mm, 24 * mm, 85 * mm, 71 * mm]))

    if plan.issues:
        story += [Spacer(1, 3 * mm), Paragraph("Findings requiring engineering action", S["h2"])]
        frows = [["Severity", "Code", "Detail"]]
        for i in plan.issues:
            frows.append([i.severity, i.code, Paragraph(i.message, S["cell"])])
        story.append(_table(frows, [20 * mm, 46 * mm, 176 * mm]))

    story.append(PageBreak())
    png = Path(drawing_png)
    if png.exists():
        from PIL import Image as PILImage
        w, h = PILImage.open(png).size
        max_w = 267 * mm
        max_h = 150 * mm
        scale = min(max_w / w, max_h / h)
        story += [Paragraph("Inspection layout", S["h2"]),
                  Paragraph("Balloons are placed outside the part outline; the leader ends at "
                            "the dimension's own definition-point cluster.", S["body"]),
                  Spacer(1, 2 * mm),
                  Image(str(png), width=w * scale, height=h * scale)]
    else:
        story += [Paragraph("Inspection layout", S["h2"]),
                  Paragraph("<font color='#b00020'><b>Rendered page missing: "
                            f"{png}</b></font> - the layout could not be shown.", S["body"])]

    story.append(PageBreak())
    story += [Paragraph("Inspection table", S["h2"]),
              Paragraph("Limits are inclusive. A dash means the drawing did not state enough to "
                        "compute a value - it does not mean zero, and it does not mean exact.",
                        S["small"]),
              Spacer(1, 2 * mm),
              _inspection_table(plan, S)]

    story.append(PageBreak())
    tools = [(k, v) for k, v in record.tools.items() if k not in ("python", "platform")]
    story += [
        Paragraph("Provenance and reproducibility", S["h2"]),
        _table([["Item", "Value"],
                ["Engine", f"{record.engine} {record.engine_version}"],
                ["Route", record.route],
                ["Generated (UTC)", record.created_utc],
                ["Python", record.tools.get("python", "-")],
                ["Platform", record.tools.get("platform", "-")],
                *[[f"library: {k}", Paragraph(str(v), S["cell"])] for k, v in tools]],
               [50 * mm, 192 * mm]),
    ]
    story += [
        Spacer(1, 2 * mm),
        Paragraph("Inputs", S["h2"]),
        _table([["File", "Bytes", "SHA-256"]] +
               [[i["path"], str(i["bytes"]), Paragraph(i["sha256"], S["small"])]
                for i in record.inputs], [95 * mm, 20 * mm, 127 * mm]),
        Paragraph("Outputs", S["h2"]),
        _table([["File", "Bytes", "SHA-256"]] +
               [[o["path"], str(o["bytes"]), Paragraph(o["sha256"], S["small"])]
                for o in record.outputs],
               [95 * mm, 20 * mm, 127 * mm]),
        Spacer(1, 2 * mm),
        Paragraph("Limitations of this report", S["h2"]),
    ]
    for lim in record.limitations:
        story.append(Paragraph(f"- {lim}", S["body"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        "The structured plan (JSON) and the XLSX table carry the same records. Tolerance data is "
        "not held only inside this PDF.", S["small"]))

    doc.build(story, canvasmaker=_numbered(title, footer))
    return out
