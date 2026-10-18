"""OpenCascade, the second kernel: every v1 operation reproduces the closed-form answer (the same answers mac's
ExpectationEngine derives), and what the language forbids is refused, never guessed."""
import math

import pytest

pytest.importorskip("OCP")

from astracad_geom.occ_build import BuildError, NotSupported, SpecError, build  # noqa: E402
from astracad_geom.props import measure  # noqa: E402


def S(*features, **top):
    return {"name": "t", "units": "mm", "features": list(features), **top}


def rect(w, h, id="s", on="front", **xy):
    return {"$op": "sketch.rectangle", "id": id, "sketched_on": on, "args": {"width": w, "height": h, **xy}}


def ext(d, id="e", **a):
    return {"$op": "feature.extrude", "id": id, "args": {"depth": d, **a}}


def hole(d, c, id="h", **a):
    return {"$op": "feature.hole", "id": id, "sketched_on": "front", "args": {"diameter": d, "center": c, **a}}


def loops(lps, id="s", on="front"):
    return {"$op": "sketch.loops", "id": id, "sketched_on": on, "args": {"loops": lps}}


def profile(pts, id="s"):
    return {"$op": "sketch.profile", "id": id, "sketched_on": "front", "args": {"points": pts}}


REVOLVE = {"$op": "feature.revolve", "id": "r", "args": {}}
L_OUTLINE = [[["L", 0, 0, 80, 0], ["L", 80, 0, 80, 10], ["L", 80, 10, 10, 10], ["L", 10, 10, 10, 60],
              ["L", 10, 60, 0, 60], ["L", 0, 60, 0, 0]]]
SLOT_PLATE = [[["L", 0, 0, 100, 0], ["L", 100, 0, 100, 50], ["L", 100, 50, 0, 50], ["L", 0, 50, 0, 0]],
              [["L", 40, 30, 60, 30], ["A", 60, 25, 60, 30, 60, 20, -1], ["L", 60, 20, 40, 20],
               ["A", 40, 25, 40, 20, 40, 30, -1]]]
R, LEN = 20, 50
CYL = [[0, 0], [LEN, 0], [LEN, R], [0, R]]


def close(got, want, rel=1e-12):
    return abs(got - want) <= rel * max(1.0, abs(want))


CASES = [
    ("prism", S(rect(60, 40), ext(20)), 48000, 8800),
    ("through hole", S(rect(60, 40), ext(20), hole(10, [30, 20], through_all=True)),
     48000 - math.pi * 25 * 20, 8800 + 2 * math.pi * 5 * 20 - 2 * math.pi * 25),
    ("blind hole", S(rect(60, 40), ext(20), hole(10, [30, 20], depth=8)),
     48000 - math.pi * 25 * 8, 8800 + 2 * math.pi * 5 * 8),
    ("Qwen's L-bracket", S(loops(L_OUTLINE), ext(8)), 10400, 2 * 1300 + 280 * 8),
    ("Qwen's slotted plate", S(loops(SLOT_PLATE), ext(6)), (5000 - 200 - 25 * math.pi) * 6,
     2 * (5000 - 200 - 25 * math.pi) + (300 + 40 + 10 * math.pi) * 6),
    ("stepped shaft (Pappus)", S(profile([[0, 0], [40, 0], [40, 10], [25, 10], [25, 15], [0, 15]]), REVOLVE),
     2 * math.pi * (40 * 10 * 5 + 25 * 5 * 12.5), None),
    ("corner fillets", S(rect(60, 40), ext(20), {"$op": "feature.fillet", "id": "f", "args": {"radius": 5}}),
     48000 - 4 * 25 * (1 - math.pi / 4) * 20, 2 * (2400 - 100 + 25 * math.pi) + 40 * (100 - 20) + 2 * math.pi * 5 * 20),
    ("corner chamfers", S(rect(60, 40), ext(20), {"$op": "feature.chamfer", "id": "c", "args": {"distance": 4}}),
     48000 - 4 * 8 * 20, 2 * (2400 - 32) + 40 * (100 - 16) + 4 * math.sqrt(2) * 4 * 20),
    ("end-circle fillets (Pappus spandrel)",
     S(profile(CYL), REVOLVE, {"$op": "feature.fillet", "id": "f", "args": {"radius": 3, "select": "end_circles"}}),
     math.pi * R * R * LEN - 2 * 2 * math.pi * (R - 3 * (10 - 3 * math.pi) / (12 - 3 * math.pi)) * 9 * (1 - math.pi / 4),
     None),
    ("end-circle chamfers", S(profile(CYL), REVOLVE,
                              {"$op": "feature.chamfer", "id": "c", "args": {"distance": 2, "select": "end_circles"}}),
     math.pi * R * R * LEN - 2 * math.pi * 4 * (R - 2 / 3), None),
    ("hole row (linear pattern)", S(rect(100, 40), ext(10), hole(6, [20, 20], through_all=True),
                                    {"$op": "feature.pattern.linear", "id": "p",
                                     "args": {"feature": "h", "count": 4, "spacing": 20, "direction": "X"}}),
     40000 - 4 * math.pi * 9 * 10, None),
    ("bolt circle (circular pattern)",
     S({"$op": "sketch.circle", "id": "s", "sketched_on": "front", "args": {"radius": 50}}, ext(10),
       hole(8, [35, 0], through_all=True),
       {"$op": "feature.pattern.circular", "id": "p", "args": {"feature": "h", "count": 6, "axis": "Z"}}),
     math.pi * 2500 * 10 - 6 * math.pi * 16 * 10, None),
    ("v1 pocket cut (symmetric)", S(rect(60, 40), ext(20), rect(20, 10, id="s2", x=20, y=15),
                                    {"$op": "feature.cut", "id": "c", "args": {"depth": 5}}), 48000 - 1000, None),
]


@pytest.mark.parametrize("name, spec, volume, area", CASES, ids=[c[0] for c in CASES])
def test_v1_operations_reproduce_the_closed_forms(name, spec, volume, area):
    m = measure(build(spec).shape)
    assert close(m["volume_mm3"], volume), (m["volume_mm3"], volume)
    if area is not None:
        assert close(m["surface_area_mm2"], area), (m["surface_area_mm2"], area)
    assert m["solids"] == 1


def test_centre_of_mass_and_bounding_box():
    m = measure(build(S(loops(L_OUTLINE), ext(8))).shape)
    # the L as a 80 x 10 bar plus a 10 x 50 upright: area-weighted centroid
    cx = (800 * 40 + 500 * 5) / 1300
    cy = (800 * 5 + 500 * 35) / 1300
    assert all(close(a, b) for a, b in zip(m["center_of_mass_mm"], [cx, cy, 4]))
    assert all(close(a, b) for a, b in zip(m["bbox_mm"], [0, 0, 0, 80, 60, 8]))


def test_a_drives_binding_is_built_in_its_final_state():
    spec = S(rect(60, 40), ext(20), {"$op": "feature.parameter", "id": "p",
                                     "args": {"target": "D1@Boss-Extrude1", "value": 30,
                                              "drives": {"feature": "e", "arg": "depth"}}})
    assert close(measure(build(spec).shape)["volume_mm3"], 72000)


@pytest.mark.parametrize("spec, error, words", [
    (S(rect(60, 40)), SpecError, "never used"),
    (S(profile([[0, -5], [40, -5], [40, 10], [0, 10]]), REVOLVE), SpecError, "y >= 0"),
    (S(rect(60, 40), ext(20), {"$op": "feature.shell", "id": "x", "args": {"thickness": 2}}), SpecError,
     "not a v1 operation"),
    (S(rect(60, 40), ext(20), {"$op": "feature.fillet", "id": "f", "args": {"radius": 2, "edges": [0, 1]}}),
     NotSupported, "SOLIDWORKS-specific"),
    (S(rect(60, 40), ext(20), {"$op": "feature.parameter", "id": "p", "args": {"target": "D1@Sketch1", "value": 9}}),
     NotSupported, "drives"),
    (S(loops([[["L", 0, 0, 10, 0], ["L", 10, 0, 10, 10]]]), ext(5)), SpecError, "not closed"),
])
def test_what_the_language_forbids_is_refused(spec, error, words):
    with pytest.raises(error, match=words):
        build(spec)


def test_an_impossible_fillet_is_a_kernel_failure_not_a_pass():
    with pytest.raises(BuildError):
        build(S(rect(60, 40), ext(20), {"$op": "feature.fillet", "id": "f", "args": {"radius": 25}}))
