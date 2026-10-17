import math

import pytest

from astracad_core.requests import interpret


def test_block_with_centred_hole():
    it = interpret("Create a 60 x 40 x 20 mm block in SolidWorks with a 10 mm hole at the centre, export STEP")
    assert it.executable and it.application == "solidworks" and it.exports == ["step"]
    assert it.expect["volume_mm3"] == pytest.approx(48000 - math.pi * 25 * 20)
    ops = [f["$op"] for f in it.spec["features"]]
    assert ops == ["sketch.rectangle", "feature.extrude", "feature.hole"]


def test_hole_without_position_is_ambiguous_not_guessed():
    it = interpret("60 x 40 x 20 mm plate with a 10 mm hole in solidworks")
    assert not it.executable and any("position" in a for a in it.ambiguities)
    assert any(f.provenance == "AI_PROPOSED" for f in it.facts)


def test_missing_unit_is_missing_not_assumed():
    it = interpret("make a cylinder diameter 30 length 25 in solidworks")
    assert not it.executable and any("unit" in m for m in it.missing)


def test_cylinder_inch_conversion_is_calculated():
    it = interpret("SolidWorks shaft diameter 1 inch length 4 inch")
    assert it.executable
    assert it.spec["features"][0]["args"]["radius"] == pytest.approx(12.7)
    assert any(f.provenance == "CALCULATED" and f.name == "diameter_mm" for f in it.facts)


def test_missing_application_reported():
    it = interpret("cylinder diameter 30 mm length 25 mm")
    assert "target CAD application" in it.missing


def test_two_applications_is_ambiguous():
    it = interpret("make a 10x10x10 mm block in creo or solidworks")
    assert it.ambiguities


def test_modify_existing_names_what_is_missing():
    it = interpret("Open my SolidWorks shaft model, change its diameter to 25 mm, verify and export a STEP file")
    assert it.intent == "modify_existing" and not it.executable
    assert it.expect["value_mm"] == 25
    assert any("which document" in m for m in it.missing)
    assert any("named model dimension" in m for m in it.missing)


def test_unrecognised():
    assert interpret("design me a gearbox").intent == "unrecognised"


def test_oversize_hole_rejected():
    it = interpret("20 x 20 x 5 mm plate with a 25 mm hole at center in solidworks")
    assert not it.executable
