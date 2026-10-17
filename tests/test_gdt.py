"""Feature control frames: parsed whole or refused with a reason, never half-guessed."""
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from astracad_inspect.gdt import (GeometricTolerance, describe, parse_dxf_tolerance,
                                  parse_unicode_frame)
from astracad_inspect.ingest.dxf import read_dxf
from astracad_inspect.ingest.pdf import read_pdf
from astracad_inspect.limits import deviation_band, resolve_limits
from astracad_inspect.model import CharKind, CheckOutcome
from astracad_inspect.plan import build_plan

FIX = Path(__file__).parent / "fixtures"
G = "{\Fgdt;%s}"


def test_position_at_mmc_with_three_datums():
    [r] = parse_dxf_tolerance(f"{G % 'j'}%%v{G % 'n'}0.05{G % 'm'}%%vA%%vB%%vC")
    assert r.tolerance == GeometricTolerance("position", 0.05, True, "MMC", ("A", "B", "C"))


def test_two_row_frame_gives_two_rows():
    rows = parse_dxf_tolerance(f"{G % 'b'}%%v0.03%%vA^J{G % 'f'}%%v0.02%%vA")
    assert [r.tolerance.symbol for r in rows] == ["perpendicularity", "parallelism"]


@pytest.mark.parametrize("content,why", [
    (f"{G % 'j'}%%vX%%vA", "not a single number"),
    ("%%v0.1%%vA", "symbol"),
    (f"{G % 'j'}%%v0.1%%v12", "not a datum letter"),
])
def test_malformed_rows_are_refused_with_a_reason(content, why):
    [r] = parse_dxf_tolerance(content)
    assert not r.resolved and why in r.unresolved_reason


def test_limits_are_zero_to_zone_and_never_carry_a_nominal():
    t = GeometricTolerance("flatness", 0.02)
    assert resolve_limits(None, t, "mm") == (0.0, 0.02) and deviation_band(t, "mm") == 0.02
    with pytest.raises(AssertionError):
        resolve_limits(5.0, t, "mm")


def test_unicode_frames_and_non_frames():
    assert describe(parse_unicode_frame("⌖ ⌀0.01 Ⓜ | A | B | C").tolerance) == \
        "position | ⌀0.01 (MMC) | A | B | C"
    assert parse_unicode_frame("⊥ 0.02 A").tolerance.datums == ("A",)
    assert parse_unicode_frame("R10") is None and parse_unicode_frame("⌀12 H7") is None


def test_dxf_fixture_rows_bonus_warning_and_escalation():
    b = read_dxf(FIX / "bracket_gdt_synthetic.dxf")
    geo = [c for c in b.characteristics if c.kind is CharKind.GEOMETRIC]
    assert len(geo) == 5
    pos = next(c for c in geo if c.tolerance.symbol == "position")
    assert any(i.code == "gdt_bonus_tolerance_not_applied" for i in pos.issues)
    assert all(k.outcome is CheckOutcome.NOT_VERIFIABLE for c in geo for k in c.checks
               if k.name == "geometry_cross_check")
    assert [u.text for u in b.uninterpreted if "%%vX" in u.text]
    plan = build_plan(b)
    census = next(c for c in plan.checks if c.name == "census_reconciled")
    assert census.outcome is CheckOutcome.PASSED
    limits_ok = next(c for c in plan.checks if c.name == "limits_independently_recomputed")
    assert limits_ok.outcome is CheckOutcome.PASSED


def test_pdf_text_frame_becomes_a_geometric_row(tmp_path):
    from matplotlib import font_manager
    pdfmetrics.registerFont(TTFont("DejaVu", font_manager.findfont("DejaVu Sans")))
    p = tmp_path / "f.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.setFont("DejaVu", 9)
    c.drawString(100, 500, "⊥ 0.02 A")
    c.drawString(100, 400, "40 ±0.1")
    c.showPage()
    c.save()
    b = read_pdf(p, "mm")
    kinds = sorted(c.kind.value for c in b.characteristics)
    assert kinds == ["geometric", "linear"]
