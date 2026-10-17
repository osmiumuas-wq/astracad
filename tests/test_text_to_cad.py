"""Text -> CAD: natural-language part requests become typed specs, or say what is missing.

Each executable request is checked for the spec it produces and the size / volume it promises
(the values a STEP re-read is later held to). Requests that leave a position open must not run.
"""
import math

import pytest

from astracad_core.requests import interpret


def ops(it):
    return [f["$op"] for f in it.spec["features"]]


def test_stepped_shaft_is_one_revolved_profile_with_step_volumes():
    it = interpret("Create a stepped shaft in SolidWorks: dia 20 x 50, dia 30 x 10, "
                   "dia 20 x 40 mm, export STEP")
    assert it.executable and ops(it) == ["sketch.profile", "feature.revolve"]
    pts = it.spec["features"][0]["args"]["points"]
    assert pts[:2] == [[0, 0], [100, 0]] and all(y >= 0 for _, y in pts)
    assert it.expect["size_mm"] == [30, 30, 100]
    want = math.pi * (100 * 50 + 225 * 10 + 100 * 40)
    assert it.expect["volume_mm3"] == pytest.approx(want)
    assert it.exports == ["step"]
    assert any(f.name == "step_unit" for f in it.facts)        # 'mm' written once, applied to all


def test_equal_adjacent_steps_merge_so_the_profile_has_no_zero_edges():
    it = interpret("solidworks shaft dia 20 mm x 30 mm, dia 20 mm x 20 mm, dia 10 mm x 5 mm")
    assert it.fact("steps") == [[20, 50], [10, 5]]
    pts = it.spec["features"][0]["args"]["points"]
    assert all(pts[i] != pts[(i + 1) % len(pts)] for i in range(len(pts)))


def test_step_without_any_unit_is_missing_not_assumed():
    it = interpret("solidworks stepped shaft dia 20 x 50, dia 30 x 10")
    assert not it.executable and any("unit" in m for m in it.missing)


def test_washer_and_tube_from_wall_thickness():
    w = interpret("SolidWorks washer OD 30 mm ID 16 mm thickness 3 mm")
    assert w.executable and w.expect["size_mm"] == [30, 30, 3]
    assert w.expect["volume_mm3"] == pytest.approx(math.pi / 4 * (900 - 256) * 3)
    t = interpret("SolidWorks tube OD 40 mm wall thickness 3 mm length 120 mm")
    assert t.executable and t.fact("inner_diameter_mm") == 34
    assert t.expect["size_mm"] == [40, 40, 120]


def test_tube_bore_not_smaller_than_od_is_refused():
    it = interpret("solidworks sleeve OD 20 mm ID 25 mm length 10 mm")
    assert not it.executable and it.ambiguities


def test_row_of_holes_uses_one_hole_and_a_linear_pattern():
    it = interpret("SolidWorks 100 x 40 x 10 mm plate with 4 x 6 mm holes at 20 mm pitch "
                   "along the width, centred")
    assert it.executable
    assert ops(it) == ["sketch.rectangle", "feature.extrude", "feature.hole",
                       "feature.pattern.linear"]
    pat = it.spec["features"][-1]["args"]
    assert pat == {"feature": "hole1", "count": 4, "spacing": 20, "direction": "X"}
    assert it.fact("hole_centres_mm") == [[20, 20], [40, 20], [60, 20], [80, 20]]
    # the same plate was built and measured by SOLIDWORKS earlier: 38 869.0266 mm^3
    assert it.expect["volume_mm3"] == pytest.approx(38869.02664470769, rel=1e-9)


def test_row_without_direction_or_position_does_not_run():
    it = interpret("solidworks 100 x 40 x 10 mm plate with 4 x 6 mm holes at 20 mm pitch")
    assert not it.executable and it.spec is None
    assert any("direction" in a for a in it.ambiguities)
    assert any("centred" in a for a in it.ambiguities)


def test_bolt_circle_on_a_plate_needs_centre_and_start_angle():
    bad = interpret("solidworks 80 x 80 x 10 mm plate with 4 x 8 mm holes on a 50 mm PCD")
    assert not bad.executable and len(bad.ambiguities) == 2
    ok = interpret("solidworks 80 x 80 x 10 mm plate with 4 x 8 mm holes on a 50 mm bolt "
                   "circle, centred, starting at 45 deg")
    assert ok.executable and [f["$op"] for f in ok.spec["features"]].count("feature.hole") == 4
    c = ok.fact("hole_centres_mm")
    assert c[0] == pytest.approx([40 + 25 / math.sqrt(2), 40 + 25 / math.sqrt(2)])


def test_bolt_circle_on_a_disc_needs_no_angle_and_says_why():
    it = interpret("solidworks flange disc diameter 120 mm thick 12 mm with 6 x 10 mm holes "
                   "on a 90 mm PCD")
    assert it.executable
    ang = next(f for f in it.facts if f.name == "first_hole_angle_deg")
    assert ang.provenance == "AI_PROPOSED" and "volume" in ang.source_text
    assert it.expect["volume_mm3"] == pytest.approx(math.pi * 3600 * 12 - 6 * math.pi * 25 * 12)


def test_overlapping_or_breakout_holes_are_refused():
    over = interpret("solidworks 80 x 80 x 10 mm plate with 8 x 20 mm holes on a 40 mm PCD, "
                     "centred, starting at 0 deg")
    assert not over.executable and "overlap" in over.ambiguities[0]
    out = interpret("solidworks disc dia 50 mm thick 5 mm with 4 x 10 mm holes on a 45 mm PCD")
    assert not out.executable and "breaks out" in out.ambiguities[0]
    tight = interpret("solidworks 100 x 40 x 10 mm plate with 4 x 6 mm holes at 5 mm pitch "
                      "along the width, centred")
    assert not tight.executable and "overlap" in tight.ambiguities[0]


def test_word_counts_and_holes_without_a_place():
    it = interpret("solidworks 120 x 50 x 8 mm plate with three 8 mm holes at 30 mm pitch "
                   "along the length, centred")
    assert it.executable and it.fact("hole_count") == 3 and it.fact("row_direction") == "X"
    nowhere = interpret("solidworks 120 x 50 x 8 mm plate with three 8 mm holes")
    assert not nowhere.executable and any("pitch" in m for m in nowhere.missing)


def test_single_hole_still_needs_a_stated_position_on_a_block():
    it = interpret("Create a 60 x 40 x 20 mm block in SolidWorks with a 10 mm hole")
    assert not it.executable and "hole position" in it.ambiguities[0]
    disc = interpret("solidworks disc dia 50 mm thick 5 mm with a central 10 mm bore")
    assert disc.executable and disc.fact("hole_centres_mm") == [[0, 0]]
