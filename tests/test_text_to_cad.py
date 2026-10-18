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
    assert ok.executable and ops(ok) == ["sketch.rectangle", "feature.extrude", "feature.hole",
                                         "feature.pattern.circular"]
    assert ok.spec["features"][-1]["args"]["count"] == 4
    c = ok.fact("hole_centres_mm")
    assert c[0] == pytest.approx([40 + 25 / math.sqrt(2), 40 + 25 / math.sqrt(2)])


def test_bolt_circle_on_a_disc_needs_no_angle_and_says_why():
    it = interpret("solidworks flange disc diameter 120 mm thick 12 mm with 6 x 10 mm holes "
                   "on a 90 mm PCD")
    assert it.executable
    assert ops(it) == ["sketch.circle", "feature.extrude", "feature.hole", "feature.pattern.circular"]
    assert it.spec["features"][-1]["args"] == {"feature": "hole1", "count": 6, "axis": "Z", "center": [0.0, 0.0]}
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


def test_centred_rectangular_pocket_on_block():
    it = interpret("Create a 60 x 40 x 20 mm block in SolidWorks with a 30 x 20 mm pocket, 5 mm deep, centred")
    assert it.executable
    assert ops(it) == ["sketch.rectangle", "feature.extrude", "sketch.rectangle", "feature.cut"]
    pock_sk = it.spec["features"][2]["args"]
    assert pock_sk == {"x": 15.0, "y": 10.0, "width": 30.0, "height": 20.0}
    assert it.spec["features"][3]["args"] == {"depth": 5.0}
    # 60*40*20 - 30*20*5 = 48000 - 3000 = 45000 mm^3 (matches mac selftest pocket baseline)
    assert it.expect["volume_mm3"] == pytest.approx(45000.0)
    assert it.fact("pocket_size_mm") == [30.0, 20.0, 5.0]
    assert it.fact("pocket_corner_mm") == [15.0, 10.0]


def test_slot_along_width_and_height():
    w_slot = interpret("SolidWorks 100 x 40 x 10 mm plate with a 40 x 10 mm slot 5 mm deep along the width, centred")
    assert w_slot.executable
    assert ops(w_slot) == ["sketch.rectangle", "feature.extrude", "sketch.rectangle", "feature.cut"]
    assert w_slot.spec["features"][2]["args"] == {"x": 30.0, "y": 15.0, "width": 40.0, "height": 10.0}
    assert w_slot.expect["volume_mm3"] == pytest.approx(100 * 40 * 10 - 40 * 10 * 5)

    h_slot = interpret("SolidWorks 100 x 60 x 10 mm plate with a 40 x 10 mm slot 5 mm deep along the height, centred")
    assert h_slot.executable
    assert h_slot.spec["features"][2]["args"] == {"x": 45.0, "y": 10.0, "width": 10.0, "height": 40.0}
    assert h_slot.expect["volume_mm3"] == pytest.approx(100 * 60 * 10 - 40 * 10 * 5)


def test_pocket_and_slot_position_ambiguities_and_proposals():
    no_pos = interpret("SolidWorks 100 x 80 x 20 mm block with a 40 x 30 mm pocket, 5 mm deep")
    assert not no_pos.executable and no_pos.spec is None
    assert any("pocket position not stated" in a for a in no_pos.ambiguities)
    pos_fact = next(f for f in no_pos.facts if f.name == "pocket_position")
    assert pos_fact.provenance == "AI_PROPOSED"

    no_ori = interpret("SolidWorks 100 x 40 x 10 mm plate with a 40 x 10 mm slot 5 mm deep, centred")
    assert not no_ori.executable and no_ori.spec is None
    assert any("slot orientation not stated" in a for a in no_ori.ambiguities)


def test_missing_depth_and_depth_exceeding_stock_refused():
    no_depth = interpret("SolidWorks 100 x 80 x 20 mm block with a 40 x 30 mm pocket, centred")
    assert not no_depth.executable
    assert any("pocket depth" in m for m in no_depth.missing)

    too_deep = interpret("SolidWorks 100 x 80 x 20 mm block with a 40 x 30 mm pocket, 25 mm deep, centred")
    assert not too_deep.executable
    assert any("exceeds part thickness" in a for a in too_deep.ambiguities)


def test_pocket_breakout_and_through_pocket():
    breaks = interpret("SolidWorks 50 x 50 x 10 mm block with a 60 x 30 mm pocket, 5 mm deep, centred")
    assert not breaks.executable
    assert any("breaks out" in a for a in breaks.ambiguities)

    thru = interpret("SolidWorks 100 x 80 x 20 mm block with a 40 x 30 mm through pocket, centred")
    assert thru.executable
    assert thru.spec["features"][3]["args"] == {"depth": 20.0}
    assert thru.expect["volume_mm3"] == pytest.approx(100 * 80 * 20 - 40 * 30 * 20)


def test_pocket_on_disc_and_disc_breakout():
    disc = interpret("SolidWorks disc dia 100 mm thick 20 mm with a centred 40 x 20 mm pocket 5 mm deep")
    assert disc.executable
    assert ops(disc) == ["sketch.circle", "feature.extrude", "sketch.rectangle", "feature.cut"]
    assert disc.spec["features"][2]["args"] == {"x": -20.0, "y": -10.0, "width": 40.0, "height": 20.0}
    want_vol = math.pi * 50**2 * 20 - 40 * 20 * 5
    assert disc.expect["volume_mm3"] == pytest.approx(want_vol)

    breaks_disc = interpret("SolidWorks disc dia 50 mm thick 10 mm with a centred 40 x 40 mm pocket 5 mm deep")
    assert not breaks_disc.executable
    assert any("breaks out of the disc" in a for a in breaks_disc.ambiguities)



def test_number_between_diameter_and_length_words_goes_to_the_right_one():
    # Found live 2026-10-18: "25 mm diameter 100 mm long" was read as diameter 100 and was executable
    def dl(q):
        it = interpret(q)
        return it.executable, it.fact("diameter"), it.fact("length")
    assert dl("SolidWorks shaft 25 mm diameter 100 mm long") == (True, 25.0, 100.0)
    assert dl("SolidWorks disc 100 mm diameter 12 mm thick") == (True, 100.0, 12.0)
    assert dl("SolidWorks disc dia 100 mm thick 12 mm") == (True, 100.0, 12.0)
    assert dl("SolidWorks disc thick 12 mm diameter 100 mm") == (True, 100.0, 12.0)
    it = interpret("SolidWorks shaft diameter 100 mm long")          # one number, two words
    assert not it.executable and "length with a unit" in it.missing


# ---- fillets and chamfers on edges named by what they are ----------------------------------------
# Volumes below were measured by SOLIDWORKS 2026 SP04.1 on 2026-10-18 (mac run, select = corners /
# end_circles); the interpreter must predict them, since the kernel is checked against the prediction.
@pytest.mark.parametrize("request_text, kernel_mm3, select", [
    ("SolidWorks shaft dia 20 mm length 50 mm with 1 mm chamfer on both ends", 15647.225810, "end_circles"),
    ("SolidWorks shaft dia 20 mm length 50 mm with both ends filleted R1 mm", 15681.597977, "end_circles"),
    ("SolidWorks stepped shaft dia 25 x 40, dia 35 x 20 mm, 1.5 mm chamfer on both ends", 38672.220168, "end_circles"),
    ("SolidWorks stepped shaft dia 25 x 40, dia 35 x 20 mm, both ends rounded R2", 38720.222084, "end_circles"),
    ("SolidWorks 60 x 40 x 20 mm block with R5 corners", 47570.796327, "corners"),
    ("SolidWorks 60 x 40 x 20 mm block with 5 mm chamfer on the corners", 47000.0, "corners"),
])
def test_edge_treatment_predicts_the_kernel_volume(request_text, kernel_mm3, select):
    it = interpret(request_text)
    assert it.executable, (it.missing, it.ambiguities)
    assert it.expect["volume_mm3"] == pytest.approx(kernel_mm3, abs=1e-5)
    treat = it.spec["features"][2]                    # straight after the body, before any cut
    assert treat["$op"] in ("feature.fillet", "feature.chamfer") and treat["args"]["select"] == select


def test_corner_round_comes_before_the_holes_and_keeps_out_of_their_way():
    it = interpret("SolidWorks 100 x 60 x 10 mm plate with R5 corners and 4 x 6 mm holes at 20 mm pitch "
                   "along the width, centred")
    assert it.executable
    assert [f["$op"] for f in it.spec["features"]][:3] == ["sketch.rectangle", "feature.extrude", "feature.fillet"]
    assert it.expect["volume_mm3"] == pytest.approx(
        60000 - 4 * 25 * (1 - math.pi / 4) * 10 - 4 * math.pi * 9 * 10)
    near = interpret("SolidWorks 100 x 20 x 10 mm plate with R9 corners and 4 x 4 mm holes at 30 mm pitch "
                     "along the width, centred")
    assert not near.executable and "reaches into the fillet" in near.ambiguities[0]


def test_bolt_holes_must_clear_the_end_chamfer():
    ok = interpret("SolidWorks flange disc diameter 120 mm thick 12 mm with 6 x 10 mm holes on a 90 mm PCD "
                   "and 1 mm chamfer on both ends")
    assert ok.executable
    assert [f["$op"] for f in ok.spec["features"]][2] == "feature.chamfer"
    assert ok.expect["volume_mm3"] == pytest.approx(
        math.pi * 3600 * 12 - 6 * math.pi * 25 * 12 - 2 * math.pi * 1 * (60 - 1 / 3))
    bad = interpret("SolidWorks flange disc diameter 120 mm thick 12 mm with 6 x 10 mm holes on a 108 mm PCD "
                    "and 2 mm chamfer on both ends")
    assert not bad.executable and "reaches into the fillet / chamfer" in bad.ambiguities[0]


@pytest.mark.parametrize("request_text, why", [
    ("SolidWorks 100 x 60 x 10 mm plate with R30 corners", "less than half the shorter side"),
    ("SolidWorks 100 x 60 x 10 mm plate with 3 mm x 30° chamfer on the corners", "only 45 deg"),
    ("SolidWorks shaft dia 20 mm length 50 mm with R1 corners", "a round part has no corners"),
    ("SolidWorks 100 x 60 x 10 mm plate with 1 mm chamfer on both ends", "say '... on the corners'"),
    ("SolidWorks 100 x 60 x 10 mm plate with R5 corners and 3 mm chamfer on the corners", "one per request"),
    ("SolidWorks shaft dia 20 mm length 50 mm with 12 mm chamfer on both ends", "less than the radius"),
    ("SolidWorks stepped shaft dia 25 x 4, dia 35 x 20 mm, 5 mm chamfer on both ends", "stay on the end step"),
])
def test_edge_treatments_that_would_be_guesses_are_refused(request_text, why):
    it = interpret(request_text)
    assert not it.executable and why in " ".join(it.ambiguities)


def test_edge_treatment_words_without_a_usable_phrase_are_missing_information():
    it = interpret("SolidWorks 100 x 60 x 10 mm plate with chamfered corners")
    assert not it.executable and "size and edges" in it.missing[0]
    tube = interpret("SolidWorks tube OD 40 mm wall thickness 3 mm length 120 mm with 1 mm chamfer on both ends")
    assert not tube.executable and any("not supported yet" in m for m in tube.missing)


def test_a_bare_corner_radius_takes_the_only_unit_written():
    it = interpret("SolidWorks 4 x 2 x 0.5 in plate with R0.25 corners")
    assert it.executable and it.fact("fillet_unit") == "in"
    assert it.spec["features"][2]["args"]["radius"] == pytest.approx(6.35)
    mixed = interpret("SolidWorks 100 x 60 x 10 mm plate with a 0.25 in hole at the centre and R5 corners")
    assert not mixed.executable and any("unit for fillet_radius" in m for m in mixed.missing)


def test_export_step_does_not_make_a_shaft_stepped():
    it = interpret("SolidWorks shaft dia 20 mm length 50 mm with 1 mm chamfer on both ends, export STEP")
    assert it.executable and [f["$op"] for f in it.spec["features"]][:2] == ["sketch.circle", "feature.extrude"]
    assert it.exports == ["step"]
    stepped = interpret("SolidWorks stepped shaft dia 20 mm x 50 mm, export STEP")      # one step only
    assert not stepped.executable and "at least two steps" in stepped.missing[0]
