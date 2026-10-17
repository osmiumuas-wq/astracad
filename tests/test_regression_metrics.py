"""Metric-level tests: the labelled-drawing scoring that §XX step 13 demands, runnable under
pytest as well as through tools/regression.py (which additionally measures the exports).

Dataset: tests/fixtures/*.dxf, each with a same-named .expected.json recorded by construction in
tools/make_fixtures.py. Method and limitations are stated in tools/regression.py and in
docs/engineering/known-limitations.md - these fixtures are synthetic, so a 1.0 here is evidence
about the code path, not about production drawings.
"""
from __future__ import annotations

import json

from conftest import FIXTURES, label

from regression import score_label


def test_every_drawing_has_a_label_and_every_label_is_marked_synthetic():
    drawings = sorted(FIXTURES.glob("*.dxf"))
    assert drawings, "no fixtures - nothing is being measured"
    for dxf in drawings:
        expected = dxf.with_suffix(".expected.json")
        assert expected.exists(), f"{dxf.name} has no label file, so it cannot be scored"
        entry = json.loads(expected.read_text(encoding="utf-8"))
        assert entry.get("synthetic") is True, f"{dxf.name} is not labelled synthetic"
        assert entry.get("notice"), f"{dxf.name} has no synthetic notice"


def test_synthetic_plate_scores_full_marks(plate_plan):
    block = score_label(label("plate_synthetic"), plate_plan)
    assert block["missed"] == []
    assert block["duplicate_label_matches"] == []
    assert block["wrong_fields"] == []
    assert block["unsupported_rows"] == []
    assert block["extraction_recall"] == 1.0
    assert block["strict_field_accuracy"] == 1.0
    assert block["precision"] == 1.0
    assert block["balloon_mapping_complete"] is True
    assert block["escalation_failures"] == []
    assert block["unlabelled_escalations"] == []


def test_the_engine_does_not_call_the_drawing_complete(plate_plan):
    """A fit pair that cannot belong to one feature and an uninterpretable free-text annotation
    must keep the gate shut: the workflow may not claim completeness while required
    characteristics remain unresolved. The ⌀20 H7 bore is no longer among them — the published
    ISO 286-2 table states its limits, so it is extracted, cross-checked, and not blocking."""
    block = score_label(label("plate_synthetic"), plate_plan)
    assert block["approvable"] is False
    assert "release_gate_blocking_findings" in block["plan_checks_failed"]
    assert "annotations_fully_interpreted" in block["plan_checks_failed"]
    assert block["blocking_rows"] == ["CH-010"]
    h7 = [r.cid for r in plate_plan.rows if "H7" in r.raw_callout and "/" not in r.raw_callout]
    assert h7 and h7[0] not in block["blocking_rows"]


def test_contradiction_blocks_only_the_characteristic_that_disagrees(contradiction_plan):
    block = score_label(label("contradiction_synthetic"), contradiction_plan)
    assert block["strict_field_accuracy"] == 1.0
    assert block["contradiction_blocked"] is True
    assert block["neighbour_passed"] is True
    assert block["blocking_rows"] == ["CH-001"]
    assert block["approvable"] is False
