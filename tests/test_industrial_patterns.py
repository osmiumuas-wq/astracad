"""Patterns measured on real SOLIDWORKS drawings (2026-10-17 benchmark), reproduced synthetically
because the SOLIDWORKS sample files may not be redistributed. Each test is one defect found by
scoring ASTraCAD against the SOLIDWORKS API's own answer key."""
from pathlib import Path

import ezdxf
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from astracad_inspect.ingest.dxf import read_dxf
from astracad_inspect.ingest.pdf import read_pdf
from astracad_inspect.model import CharKind, Deviations


def _doc():
    doc = ezdxf.new("R2010", setup=True)
    for st in doc.dimstyles:
        st.dxf.dimlfac = 1.0
    doc.header["$INSUNITS"] = 4
    return doc


def test_prefixed_placeholder_reads_the_rendered_block_with_its_tolerance(tmp_path):
    """SOLIDWORKS writes '%%c<>' and puts '%%c', '0.988', '+0.000', '-0.005' in the block."""
    doc = _doc()
    msp = doc.modelspace()
    msp.add_line((0, 0), (25, 0))
    d = msp.add_linear_dim(base=(12.5, -8), p1=(0, 0), p2=(25, 0),
                           text="%%c25 +0.000/-0.005")
    d.render()
    d.dimension.dxf.text = "%%c<>"
    p = tmp_path / "ph.dxf"
    doc.saveas(p)
    [c] = read_dxf(p).characteristics
    assert c.kind is CharKind.DIAMETER and c.nominal == 25.0
    assert c.tolerance == Deviations(upper=0.0, lower=-0.005)


def test_placeholder_without_a_block_uses_the_measurement_at_dimdec(tmp_path):
    doc = _doc()
    msp = doc.modelspace()
    msp.add_line((0, 0), (20.004, 0))
    d = msp.add_linear_dim(base=(10, -8), p1=(0, 0), p2=(20.004, 0), override={"dimdec": 2})
    d.render()
    d.dimension.dxf.text = "%%c<>"
    d.dimension.dxf.discard("geometry")
    p = tmp_path / "nb.dxf"
    doc.saveas(p)
    [c] = read_dxf(p).characteristics
    assert c.nominal == 20.0 and c.kind is CharKind.DIAMETER


def test_a_dimension_with_an_outlier_scale_factor_blocks(tmp_path):
    """foodprocessor sample: a 1:1 sheet printing '15' for a 150 mm span via DIMLFAC 0.1."""
    doc = _doc()
    msp = doc.modelspace()
    for x in (0, 40, 80):
        msp.add_linear_dim(base=(x + 10, -8), p1=(x, 0), p2=(x + 20, 0)).render()
    msp.add_linear_dim(base=(75, -30), p1=(0, -20), p2=(150, -20),
                       override={"dimlfac": 0.1}).render()
    p = tmp_path / "lfac.dxf"
    doc.saveas(p)
    chars = read_dxf(p).characteristics
    odd = [c for c in chars if any(i.code == "dimension_scale_factor_outlier" for i in c.issues)]
    assert len(odd) == 1 and odd[0].nominal == 15.0 and odd[0].is_blocking
    assert "150.0000" in next(i.message for i in odd[0].issues
                              if i.code == "dimension_scale_factor_outlier")


def test_sheet_numbers_that_are_not_callouts_are_classified(tmp_path):
    """Border zone labels, balloon item numbers and table rows were 290 of 302 false PDF rows."""
    p = tmp_path / "sheet.pdf"
    c = canvas.Canvas(str(p), pagesize=landscape(A3))
    W, H = landscape(A3)
    c.setLineWidth(0.3)
    c.rect(10 * mm, 10 * mm, W - 20 * mm, H - 20 * mm)
    c.setFont("Helvetica", 10)
    c.drawString(W / 2, H - 7 * mm, "3")                          # border zone label
    c.circle(120 * mm, 200 * mm, 4.6 * mm)                        # balloon
    c.drawCentredString(120 * mm, 200 * mm - 1.3 * mm, "5")
    for i, y in enumerate((60, 66, 72)):                          # BOM rows: ruled grid
        c.line(250 * mm, y * mm, 330 * mm, y * mm)
    for x in (250, 270, 330):
        c.line(x * mm, 60 * mm, x * mm, 72 * mm)
    c.drawString(255 * mm, 62 * mm, "1")
    c.drawString(280 * mm, 62 * mm, "1500")
    c.drawString(255 * mm, 68 * mm, "2")
    c.drawString(280 * mm, 68 * mm, "907.24")
    c.line(60 * mm, 120 * mm, 100 * mm, 120 * mm)                 # a real dimension
    c.drawString(76 * mm, 122 * mm, "40")
    c.circle(58 * mm, 151.3 * mm, 1.3 * mm)                       # a drawn diameter glyph
    c.line(56.3 * mm, 149.3 * mm, 59.7 * mm, 153.3 * mm)
    c.drawString(60 * mm, 150 * mm, "25")
    c.showPage()
    c.save()
    b = read_pdf(p, "mm")
    got = sorted((ch.kind.value, ch.nominal) for ch in b.characteristics)
    assert got == [("diameter", 25.0), ("linear", 40.0)]
    kinds = {r.dxftype for r in b.census}
    assert {"PDF_TEXT:border zone label", "PDF_TEXT:balloon item number",
            "PDF_TEXT:table cell"} <= kinds
    dia = next(ch for ch in b.characteristics if ch.kind is CharKind.DIAMETER)
    assert any("vector graphics" in h.detail for h in dia.history)
