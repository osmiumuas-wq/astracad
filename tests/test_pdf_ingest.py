"""Vector-PDF route: callouts read from text, stacked tolerances joined, nothing over-claimed."""
import json
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from astracad_inspect.ingest.pdf import TextRun, group_runs, read_pdf
from astracad_inspect.model import CheckOutcome, ReviewStatus
from astracad_inspect.pipeline import inspect_drawing, read_drawing
from astracad_inspect.units import UnitError

FIX = Path(__file__).parent / "fixtures"
PDF = FIX / "plate_synthetic.pdf"


@pytest.fixture(scope="module")
def bundle():
    return read_pdf(PDF, "mm")


def _norm(s):
    from astracad_inspect.callouts import normalise
    return normalise(s).replace(" ", "")


def test_pdf_reads_the_same_callouts_as_the_dxf_it_was_drawn_from(bundle):
    exp = json.loads((FIX / "plate_synthetic.expected.json").read_text(encoding="utf-8"))
    got = {_norm(c.raw_callout): c for c in bundle.characteristics}
    resolved = [e for e in exp["expected_characteristics"] if e["resolved"]]
    found = 0
    for e in resolved:
        c = got.get(_norm(e["raw_contains"]))
        if c is None:
            continue
        found += 1
        assert c.kind.value == e["kind"] and c.nominal == e["nominal"]
        assert (list(c.limits) if c.limits else None) == e["limits"]
    assert found == len(resolved)          # every callout the DXF route resolves


def test_unresolved_and_ambiguous_text_is_escalated_not_invented(bundle):
    texts = {_norm(u.text) for u in bundle.uninterpreted}
    assert _norm("Ø10 H7/g6") in texts and _norm("86.98 87.05") in texts
    assert not any("Aluminium" in u.text for u in bundle.uninterpreted)


def test_every_pdf_characteristic_needs_review_and_is_not_cross_checked(bundle):
    for c in bundle.characteristics:
        assert c.review is ReviewStatus.NEEDS_REVIEW
        assert any(i.code == "pdf_text_only" for i in c.issues)
        assert [k.outcome for k in c.checks if k.name == "geometry_cross_check"] == \
            [CheckOutcome.NOT_VERIFIABLE]
        assert c.source.entity_type == "PDF_TEXT" and c.source.document_sha256 == bundle.sha256


def test_stacked_rotated_tolerance_is_joined(bundle):
    c = next(c for c in bundle.characteristics if c.raw_callout.startswith("50"))
    assert c.limits == (50.0, 50.2) and "+" in c.source.entity_handle


def test_units_are_never_assumed():
    with pytest.raises(UnitError):
        read_pdf(PDF)


def test_unknown_type_refused(tmp_path):
    p = tmp_path / "x.dwg"
    p.write_bytes(b"x")
    with pytest.raises(ValueError):
        read_drawing(p, "mm")


def _pdf(path, draw):
    c = canvas.Canvas(str(path), pagesize=A4)
    draw(c)
    c.showPage()
    c.save()
    return path


def test_orphan_deviation_is_not_attached_to_a_guess(tmp_path):
    def draw(c):
        c.setFont("Helvetica", 9)
        c.drawString(100, 500, "40")
        c.setFont("Helvetica", 5)
        c.drawString(300, 300, "+0.1")          # far from any nominal
    b = read_pdf(_pdf(tmp_path / "o.pdf", draw), "mm")
    assert [c.nominal for c in b.characteristics] == [40.0]
    assert [u.text for u in b.uninterpreted] == ["+0.1"]


def test_scanned_pdf_is_refused(tmp_path):
    from PIL import Image
    img = tmp_path / "scan.png"
    Image.new("RGB", (50, 50), "white").save(img)
    p = _pdf(tmp_path / "s.pdf", lambda c: c.drawImage(str(img), 10, 10, 50, 50))
    with pytest.raises(ValueError, match="OCR"):
        read_pdf(p, "mm")


def test_group_runs_inline_and_stack_in_rotated_frame():
    runs = [TextRun(0, 1, "50", 90.0, 3.0, (10.0, 10.0, 13.0, 16.0)),
            TextRun(1, 1, "+0.2", 90.0, 1.8, (9.5, 16.5, 11.3, 20.0)),    # left of centre = upper
            TextRun(2, 1, "-0", 90.0, 1.8, (12.0, 16.5, 13.8, 18.5)),
            TextRun(3, 1, "2x", 0.0, 3.0, (50.0, 50.0, 54.0, 53.0)),
            TextRun(4, 1, "Ø8", 0.0, 3.0, (55.0, 50.0, 59.0, 53.0))]
    g = {r.text for r in group_runs(runs)}
    assert g == {"50 +0.2/-0", "2x Ø8"}


def test_pdf_pipeline_delivers_but_is_not_releasable(tmp_path):
    res = inspect_drawing(PDF, tmp_path / "out", declared_unit="mm")
    assert res.delivered and not res.plan.approvable
    assert res.record.route == "pdf-vector"
    census = [c for c in res.plan.checks if c.name == "census_reconciled"]
    assert census[0].outcome is CheckOutcome.NOT_VERIFIABLE
