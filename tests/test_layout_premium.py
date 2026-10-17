"""Balloon layout: adjacent to the callout on clear paper, uncrossed, read in order, and the
pipeline/viewer pieces that depend on it (sheet pages, layout pages, old-run upgrade)."""
import json
import math

import ezdxf
import numpy as np
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from astracad_inspect.balloons import (Occupancy, layout_balloons, leader_crossings, on_ink,
                                       overlaps, segments_cross)
from astracad_inspect.model import Characteristic, CharKind, SourceLocation
from conftest import dxf


def _char(cid, x, y, box=None, page=1):
    return Characteristic(cid=cid, kind=CharKind.LINEAR, raw_callout="10", unit="mm", nominal=10.0,
                          source=SourceLocation("d", "s", "A", page, cid, "T", "", x, y, text_box=box))


def _blank(frame, cells_per_mm=2, ink_boxes=()):
    x0, y0, x1, y1 = frame
    W, H = int((x1 - x0) * cells_per_mm), int((y1 - y0) * cells_per_mm)
    a = np.zeros((H, W), dtype=bool)
    for bx0, by0, bx1, by1 in ink_boxes:
        a[int((y1 - by1) * cells_per_mm):int((y1 - by0) * cells_per_mm),
          int((bx0 - x0) * cells_per_mm):int((bx1 - x0) * cells_per_mm)] = True
    return Occupancy(a, frame)


def test_balloons_sit_next_to_their_callout_on_clear_paper():
    frame = (0.0, 0.0, 200.0, 120.0)
    part = (40.0, 30.0, 160.0, 90.0)
    occ = _blank(frame, ink_boxes=[part])                  # a solid part: no balloon may cover it
    boxes = {"CH-001": (60.0, 92.0, 72.0, 96.0), "CH-002": (120.0, 92.0, 132.0, 96.0),
             "CH-003": (20.0, 50.0, 28.0, 54.0), "CH-004": (100.0, 20.0, 112.0, 24.0)}
    chars = [_char(c, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, b) for c, b in boxes.items()]
    lay = layout_balloons(chars, part, 4.0, 2.0, 14.0, frames={1: frame}, occupancy={1: occ})
    assert len(lay.balloons) == 4 and not lay.unplaced
    assert all(b.style == "adjacent" for b in lay.balloons)
    assert not overlaps(lay.balloons) and not leader_crossings(lay.balloons)
    assert not on_ink(lay.balloons, {1: occ})
    for b in lay.balloons:
        tb = boxes[b.cid]
        # the balloon does not cover its own callout, and its leader is short (under a diameter)
        q = (min(max(b.centre[0], tb[0]), tb[2]), min(max(b.centre[1], tb[1]), tb[3]))
        assert math.dist(q, b.centre) >= b.radius
        assert math.dist(b.leader[0], b.leader[-1]) < 2 * b.radius
        # the leader starts on the callout's box
        assert tb[0] - 1e-9 <= b.leader[0][0] <= tb[2] + 1e-9 and tb[1] - 1e-9 <= b.leader[0][1] <= tb[3] + 1e-9


def test_numbers_follow_reading_order():
    frame = (0.0, 0.0, 300.0, 200.0)
    occ = _blank(frame)
    pts = {"CH-001": (250, 30), "CH-002": (40, 170), "CH-003": (150, 100), "CH-004": (260, 170),
           "CH-005": (30, 30)}
    chars = [_char(c, x, y, (x - 4, y - 2, x + 4, y + 2)) for c, (x, y) in pts.items()]
    lay = layout_balloons(chars, (0, 0, 300, 200), frames={1: frame}, occupancy={1: occ})
    order = [b.cid for b in sorted(lay.balloons, key=lambda b: b.number)]
    # top band left to right, then middle, then bottom band left to right
    assert order == ["CH-002", "CH-004", "CH-003", "CH-005", "CH-001"]
    assert {c.cid: c.balloon_no for c in chars} == {b.cid: b.number for b in lay.balloons}


def test_ring_fallback_has_no_crossing_leaders():
    # anchors deliberately listed so that nearest-slot greed alone would cross leaders
    chars = [_char(f"CH-{i:03d}", x, y) for i, (x, y) in enumerate(
        [(90, 40), (10, 40), (80, 10), (20, 10), (50, 25), (60, 45), (40, 5)], start=1)]
    lay = layout_balloons(chars, (0.0, 0.0, 100.0, 50.0))
    assert not lay.unplaced and not overlaps(lay.balloons)
    assert not leader_crossings(lay.balloons)


def test_segments_cross_is_proper_crossing_only():
    assert segments_cross((0, 0), (10, 10), (0, 10), (10, 0))
    assert not segments_cross((0, 0), (10, 0), (10, 0), (20, 5))      # touching at an end
    assert not segments_cross((0, 0), (10, 0), (0, 1), (10, 1))       # parallel


def test_balloon_size_is_a_printed_size_in_an_inch_drawing(tmp_path):
    from astracad_inspect.pipeline import inspect_drawing
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 1                      # inches
    doc.header["$MEASUREMENT"] = 0
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (4, 0), (4, 2), (0, 2)], close=True)
    msp.add_linear_dim(base=(2, 2.5), p1=(0, 2), p2=(4, 2), dimstyle="EZDXF",
                       override={"dimlfac": 1, "dimtxt": 0.12, "dimasz": 0.1}).render()
    p = tmp_path / "inch.dxf"
    doc.saveas(p)
    res = inspect_drawing(p, tmp_path / "out")
    b = res.plan.layout.balloons[0]
    assert res.plan.unit == "in"
    assert abs(b.radius - 4.0 / 25.4) < 1e-9          # 4 mm, not 4 inches


def test_stacked_item_balloons_are_not_read_as_dimensions(tmp_path):
    """SOLIDWORKS 'leg': item balloons stacked close together, each with a leader."""
    from astracad_inspect.ingest.pdf import read_pdf
    p = tmp_path / "sheet.pdf"
    c = canvas.Canvas(str(p), pagesize=landscape(A3))
    c.setLineWidth(0.3)
    c.setFont("Helvetica", 24)
    for k, (n, y) in enumerate((("5", 240), ("9", 224), ("11", 208))):
        c.circle(380 * mm, y * mm, 4.6 * mm)
        c.line(375.4 * mm, y * mm, 330 * mm, (y - 20) * mm)          # leader to the part
        c.drawCentredString(380 * mm, y * mm - 3 * mm, n)
    c.line(60 * mm, 120 * mm, 100 * mm, 120 * mm)                     # a real dimension
    c.setFont("Helvetica", 10)
    c.drawString(76 * mm, 122 * mm, "40")
    c.showPage()
    c.save()
    b = read_pdf(p, "mm")
    assert [(ch.kind.value, ch.nominal) for ch in b.characteristics] == [("linear", 40.0)]
    assert any(r.dxftype == "PDF_TEXT:balloon item number" and r.total == 3 for r in b.census)


def test_every_page_gets_a_sheet_and_a_layout(tmp_path):
    from astracad_inspect.pipeline import inspect_drawing
    p = tmp_path / "two.pdf"
    c = canvas.Canvas(str(p), pagesize=landscape(A3))
    for page, (txt, y) in enumerate((("40", 120), ("55", 150)), start=1):
        c.setLineWidth(0.3)
        c.line(60 * mm, y * mm - 2 * mm, 100 * mm, y * mm - 2 * mm)
        c.setFont("Helvetica", 10)
        c.drawString(76 * mm, y * mm, txt)
        c.showPage()
    c.save()
    res = inspect_drawing(p, tmp_path / "out", declared_unit="mm")
    out = tmp_path / "out"
    sheet = json.loads((out / "two.sheet.json").read_text(encoding="utf-8"))
    assert [pg["page"] for pg in sheet["pages"]] == [1, 2]
    assert (out / "two_layout.png").exists() and (out / "two_layout_p2.png").exists()
    pages = {b.cid: b.page for b in res.plan.layout.balloons}
    assert sorted(pages.values()) == [1, 2]
    # the page-2 balloon is next to the page-2 callout, not at page-1 coordinates
    b2 = next(b for b in res.plan.layout.balloons if b.page == 2)
    assert abs(b2.leader[0][1] - 150) < 8
    checks = {c.name: c.outcome.value for c in res.plan.checks}
    assert checks["leaders_do_not_cross"] == "passed"
    assert checks["balloons_clear_of_drawing"] == "passed"


def test_layout_png_has_a_title_band_above_the_sheet(tmp_path):
    from PIL import Image
    from astracad_inspect.pipeline import inspect_drawing
    inspect_drawing(dxf("plate_synthetic"), tmp_path)
    sheet = Image.open(tmp_path / "plate_synthetic_sheet.png")
    lay = Image.open(tmp_path / "plate_synthetic_layout.png")
    assert lay.width == sheet.width and lay.height > sheet.height
    band = np.asarray(lay.convert("L"))[: lay.height - sheet.height]
    assert band.mean() < 80                           # the dark title band


def test_old_run_gets_its_interactive_sheet_on_first_view(tmp_path):
    from astracad_web.server import Workspace
    ws = Workspace(db=tmp_path / "t.db", runs=[tmp_path / "runs"])
    r = ws.run_inspection(str(dxf("plate_synthetic")), None, None)
    folder = tmp_path / "runs" / r["folder"].split("\\")[-1].split("/")[-1]
    (folder / "plate_synthetic.sheet.json").unlink()
    (folder / "plate_synthetic_sheet.png").unlink()
    d = ws.inspection(r["key"])
    assert d["sheet"] and d["sheet"]["pages"][0]["width_px"] > 0
    meta = json.loads((folder / "plate_synthetic.sheet.json").read_text(encoding="utf-8"))
    assert meta["generated_later"] is True
