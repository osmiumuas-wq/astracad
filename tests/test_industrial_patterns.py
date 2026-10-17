"""Patterns measured on real SOLIDWORKS drawings (2026-10-17 benchmark), reproduced synthetically
because the SOLIDWORKS sample files may not be redistributed. Each test is one defect found by
scoring ASTraCAD against the SOLIDWORKS API's own answer key."""
from pathlib import Path

import ezdxf
import pytest
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


def _frame_pdf(path, frames):
    """Draw feature control frames as graphics (like SOLIDWORKS): boxes, a stroked symbol, the
    zone and datum letters as text."""
    c = canvas.Canvas(str(path), pagesize=landscape(A3))
    c.setLineWidth(0.25)
    for k, (symbol, zone, datums) in enumerate(frames):
        x, y, h = 60 * mm, (250 - 22 * k) * mm, 7 * mm
        cells = [h, 14 * mm] + [h] * len(datums)
        c.rect(x, y, sum(cells), h)
        xx = x
        for w in cells[:-1]:
            xx += w
            c.line(xx, y, xx, y + h)
        sx, sy, r = x + h / 2, y + h / 2, h * 0.3
        if symbol == "position":
            c.circle(sx, sy, r * 0.7); c.line(sx - r, sy, sx + r, sy); c.line(sx, sy - r, sx, sy + r)
        elif symbol == "perpendicularity":
            c.line(sx - r, sy - r, sx + r, sy - r); c.line(sx, sy - r, sx, sy + r)
        elif symbol == "parallelism":
            c.line(sx - r, sy - r, sx - r * 0.2, sy + r); c.line(sx + r * 0.2, sy - r, sx + r, sy + r)
        elif symbol == "flatness":
            p = c.beginPath(); p.moveTo(sx - r, sy - r * 0.6); p.lineTo(sx + r * 0.5, sy - r * 0.6)
            p.lineTo(sx + r, sy + r * 0.6); p.lineTo(sx - r * 0.5, sy + r * 0.6); p.close(); c.drawPath(p)
        elif symbol == "circularity":
            c.circle(sx, sy, r)
        elif symbol == "cylindricity":
            c.circle(sx, sy, r * 0.6); c.line(sx - r, sy - r, sx - r * 0.3, sy + r); c.line(sx + r * 0.3, sy - r, sx + r, sy + r)
        c.setFont("Helvetica", 9)
        c.drawString(x + h + 2 * mm, y + 2.2 * mm, zone)
        xx = x + h + 14 * mm
        for d in datums:
            c.drawString(xx + 2.2 * mm, y + 2.2 * mm, d)
            xx += h
    c.showPage()
    c.save()
    return path


def test_frames_drawn_as_graphics_are_read_with_symbol_and_datums(tmp_path):
    frames = [("position", "0.05", ["A", "B", "C"]), ("perpendicularity", "0.02", ["A"]),
              ("parallelism", "0.03", ["B"]), ("flatness", "0.01", []),
              ("circularity", "0.008", []), ("cylindricity", "0.015", [])]
    b = read_pdf(_frame_pdf(tmp_path / "f.pdf", frames), "mm")
    got = {(c.tolerance.symbol, c.tolerance.zone, c.tolerance.datums)
           for c in b.characteristics if c.kind is CharKind.GEOMETRIC}
    want = {(s, float(z), tuple(d)) for s, z, d in frames}
    assert got == want
    assert not [c for c in b.characteristics if c.kind is not CharKind.GEOMETRIC]


def test_unrecognised_symbol_is_escalated_not_guessed(tmp_path):
    p = tmp_path / "u.pdf"
    c = canvas.Canvas(str(p), pagesize=landscape(A3))
    x, y, h = 60 * mm, 200 * mm, 7 * mm
    c.rect(x, y, h + 14 * mm, h); c.line(x + h, y, x + h, y + h)
    for k in range(4):                                     # a scribble: not an ISO 1101 symbol
        c.line(x + 1.5 * mm + k * mm, y + 1.5 * mm, x + 2 * mm + k * mm, y + 5.5 * mm)
    c.setFont("Helvetica", 9); c.drawString(x + h + 2 * mm, y + 2.2 * mm, "0.10")
    c.showPage(); c.save()
    b = read_pdf(p, "mm")
    assert not b.characteristics
    assert any("not recognised" in u.reason for u in b.uninterpreted)


def _rotated_phi_pdf(path, deg):
    """A ⌀ drawn as graphics (ring + upright stroke, SOLIDWORKS 'bolt-assembly' style) before a number,
    the whole callout rotated along an angled leader."""
    import math
    c = canvas.Canvas(str(path), pagesize=landscape(A3))
    c.setLineWidth(0.3)
    c.saveState()
    c.translate(150 * mm, 150 * mm)
    c.rotate(deg)
    c.circle(-3.2 * mm, 1.6 * mm, 1.5 * mm)
    c.line(-3.2 * mm, -0.6 * mm, -3.2 * mm, 3.8 * mm)
    c.setFont("Helvetica", 12)
    c.drawString(0, 0, "16")
    c.restoreState()
    c.line(100 * mm, 140 * mm, 140 * mm, 148 * mm)          # a leader passing nearby
    c.showPage()
    c.save()


@pytest.mark.parametrize("deg", [0, 21.5, 315])
def test_diameter_glyph_found_on_rotated_callouts(tmp_path, deg):
    p = tmp_path / "phi.pdf"
    _rotated_phi_pdf(p, deg)
    b = read_pdf(p, "mm")
    assert [(ch.kind.value, ch.nominal) for ch in b.characteristics] == [("diameter", 16.0)]


def test_number_starting_a_note_and_a_roughness_value_are_not_dimensions(tmp_path):
    p = tmp_path / "notes.pdf"
    c = canvas.Canvas(str(p), pagesize=landscape(A3))
    c.setLineWidth(0.3)
    c.setFont("Helvetica", 10)
    c.drawString(60 * mm, 60 * mm, "90")
    c.drawString(66 * mm, 60 * mm, "ROTATION FOR FULL FLOW")
    # surface finish symbol: short arm, long arm rising at 60 deg, bar at its top; value above-left
    vx, vy = 200 * mm, 100 * mm
    c.line(vx - 7 * mm, vy + 12 * mm, vx, vy)
    c.line(vx, vy, vx + 12.5 * mm, vy + 21.2 * mm)
    c.line(vx + 12.5 * mm, vy + 21.2 * mm, vx + 18.5 * mm, vy + 21.2 * mm)
    c.drawString(vx - 4 * mm, vy + 13 * mm, "32")
    c.line(60 * mm, 120 * mm, 100 * mm, 120 * mm)            # and one real dimension
    c.drawString(76 * mm, 122 * mm, "40")
    c.showPage()
    c.save()
    b = read_pdf(p, "mm")
    assert [(ch.kind.value, ch.nominal) for ch in b.characteristics] == [("linear", 40.0)]
    assert any(u.text == "32" and "surface finish" in u.reason for u in b.uninterpreted)
    assert any(r.dxftype == "PDF_TEXT:number at the start of a note" for r in b.census)


def test_ordinate_origin_zero_is_not_a_characteristic(tmp_path):
    doc = ezdxf.new("R2010", setup=True)
    doc.dimstyles.get("Standard").dxf.dimlfac = 1          # ezdxf's setup default is 100
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (60, 0), (60, 30), (0, 30)], close=True)
    for x in (0, 25, 60):
        msp.add_ordinate_x_dim(feature_location=(x, 30), offset=(0, 10), origin=(0, 0),
                               override={"dimlfac": 1}).render()
    p = tmp_path / "ord.dxf"
    doc.saveas(p)
    b = read_dxf(p)
    assert sorted(ch.nominal for ch in b.characteristics) == [25.0, 60.0]
    assert any(r.dxftype == "DIMENSION:ordinate origin" and r.total == 1 for r in b.census)


def test_hole_table_tag_beside_a_hole_is_not_a_dimension(tmp_path):
    p = tmp_path / "tags.pdf"
    c = canvas.Canvas(str(p), pagesize=landscape(A3))
    c.setLineWidth(0.3)
    c.setFont("Helvetica", 10)
    c.circle(100 * mm, 100 * mm, 8 * mm)
    c.drawString(107 * mm, 107 * mm, "3")                    # tag touching the hole's edge, no line
    c.line(60 * mm, 140 * mm, 100 * mm, 140 * mm)           # a real dimension on its line
    c.drawString(78 * mm, 141.5 * mm, "40")
    c.showPage()
    c.save()
    b = read_pdf(p, "mm")
    assert [(ch.kind.value, ch.nominal) for ch in b.characteristics] == [("linear", 40.0)]
    assert any("feature tag" in r.dxftype for r in b.census)
