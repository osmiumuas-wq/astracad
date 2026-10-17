"""Raster route: two OCR engines must agree; what they do not agree on is escalated, never used."""
import json
from pathlib import Path

import pytest

from astracad_inspect.ingest.raster import agree, engines_available

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("a,b,want", [
    ("100 ±0.1", "100 ±0.1", "100 ±0.1"),
    ("Ø20 H7", "020 H7", "⌀20H7"),          # RapidOCR's ⌀->0 confusion, ⌀ seen by the other
    ("020 H7", "Ø20 H7", "⌀20H7"),
    ("450", "45°", "45°"),                  # Windows OCR's °->0 confusion, ° seen by the other
])
def test_agreement_tolerates_only_named_symbol_confusions(a, b, want):
    assert agree(a, b)[0] == want


@pytest.mark.parametrize("a,b", [
    ("020 H7", "020 H7x"), ("5/10.00", "010.05/10.00"), ("45", "46"), ("450", "45"),
    ("020", "020°"), (None, "45°"), ("2xØ O THRU", "2x 020 THRU"),
    ("020", "O20"),                          # neither engine saw a diameter sign: not invented
])
def test_disagreement_is_never_merged(a, b):
    assert agree(a, b)[0] is None


ENGINES = engines_available()
needs_ocr = pytest.mark.skipif(any(v != "available" for v in ENGINES.values()),
                               reason=f"OCR engines not available here: {ENGINES}")


@needs_ocr
def test_scan_route_accepts_only_correct_values():
    from astracad_inspect.ingest.raster import read_raster
    b = read_raster(FIX / "plate_synthetic_scan300.png", "mm")
    label = json.loads((FIX / "plate_synthetic.expected.json").read_text(encoding="utf-8"))
    truth = {}
    for e in label["expected_characteristics"]:
        if e["resolved"]:
            from astracad_inspect.callouts import normalise
            truth[normalise(e["raw_contains"]).replace(" ", "")] = e
    accepted = 0
    for c in b.characteristics:
        e = truth.get(c.raw_callout.replace(" ", ""))
        assert e is not None, f"accepted a value that is not on the drawing: {c.raw_callout!r}"
        assert c.kind.value == e["kind"] and c.nominal == e["nominal"]
        assert (list(c.limits) if c.limits else None) == e["limits"]
        assert c.review.value == "needs_review"
        accepted += 1
    assert accepted >= 3                      # measured 3 of 9 on 2026-10-17; precision is the gate
    assert not any(c.raw_callout == "450" for c in b.characteristics)
    assert any("450" in (u.text + u.reason) for u in b.uninterpreted) or \
        any(c.raw_callout == "45°" for c in b.characteristics)


@needs_ocr
def test_scan_pipeline_delivers_but_is_never_releasable(tmp_path):
    from astracad_inspect.pipeline import inspect_drawing
    res = inspect_drawing(FIX / "plate_synthetic_scan300.png", tmp_path, declared_unit="mm")
    assert res.delivered and not res.plan.approvable and res.record.route == "raster-ocr"


@needs_ocr
def test_scan_balloons_and_border_labels_are_not_callouts(tmp_path):
    """Measured on a SOLIDWORKS mould base: balloons and zone labels were most OCR false rows."""
    from matplotlib import font_manager
    from PIL import Image, ImageDraw, ImageFont

    from astracad_inspect.ingest.raster import read_raster
    dpi = 300
    px = lambda v_mm: int(v_mm / 25.4 * dpi)            # noqa: E731
    img = Image.new("RGB", (px(297), px(210)), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(font_manager.findfont("DejaVu Sans"), px(4.5))
    d.rectangle([px(10), px(10), px(287), px(200)], outline="black", width=3)
    d.text((px(148), px(3)), "4", fill="black", font=font)                 # zone label in the margin
    cx, cy, r = px(100), px(60), px(5)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline="black", width=3)   # balloon
    d.text((cx - px(1.4), cy - px(2.6)), "7", fill="black", font=font)
    d.line([px(60), px(150), px(120), px(150)], fill="black", width=3)     # a real dimension
    d.text((px(84), px(143)), "60", fill="black", font=font)
    p = tmp_path / "scan.png"
    img.save(p, dpi=(dpi, dpi))
    b = read_raster(p, "mm")
    assert [c.nominal for c in b.characteristics] == [60.0]
    # the classifier itself, on the known text boxes (whether an engine reads a lone digit is the
    # engines' business; what a read digit in a circle means is ours)
    from astracad_inspect.ingest.raster import _classify_region, raster_geometry
    geo = raster_geometry(img, dpi)
    mm = 25.4 / dpi
    balloon_box = (cx - px(1.6), cy - px(2.6), cx + px(1.6), cy + px(2.6))
    assert _classify_region(geo, balloon_box, "7", img.height, mm) == "balloon item number"
    assert _classify_region(geo, (px(148), px(3), px(151), px(8)), "4", img.height, mm)         == "border zone label"
    assert _classify_region(geo, (px(84), px(143), px(92), px(149)), "60", img.height, mm) is None
