"""Part Language v2 in OpenCascade: every operation against a closed form (part-language-v2.md), and the language's
own refusals. These are the second kernel's half of M1; the SOLIDWORKS half must give the same numbers."""
import math

import pytest

pytest.importorskip("OCP")

from astracad_geom.occ_build import QueryMismatch, SpecError, build  # noqa: E402
from astracad_geom.props import measure  # noqa: E402


def V2(*features):
    return {"name": "t", "units": "mm", "language": 2, "features": list(features)}


def rect(w, h, id="s", on="front", **xy):
    return {"$op": "sketch.rectangle", "id": id, "sketched_on": on, "args": {"width": w, "height": h, **xy}}


def circle(r, id="c", on="front", x=0, y=0):
    return {"$op": "sketch.circle", "id": id, "sketched_on": on, "args": {"radius": r, "x": x, "y": y}}


def ext(d, id="e", **a):
    return {"$op": "feature.extrude", "id": id, "args": {"depth": d, **a}}


def cut(d, id="k", **a):
    return {"$op": "feature.cut", "id": id, "args": {"depth": d, **a}}


def plane(id, **a):
    return {"$op": "ref.plane", "id": id, "args": a}


def loop(pts):
    return [["L", *pts[i], *pts[(i + 1) % len(pts)]] for i in range(len(pts))]


def m(spec):
    return measure(build(spec).shape)


def close(a, b, rel=1e-9):
    return abs(a - b) <= rel * max(1.0, abs(b))


BOX = [rect(60, 40), ext(20)]


# ---- planes, frames, directions ----
def test_offset_plane_and_extrude_directions():
    r = m(V2(plane("p20", base="front", offset=20), rect(30, 10, on="p20"), ext(5)))
    assert close(r["volume_mm3"], 1500) and close(r["bbox_mm"][2], 20) and close(r["bbox_mm"][5], 25)
    r = m(V2(rect(30, 10), ext(5, direction="reverse")))
    assert close(r["bbox_mm"][2], -5) and close(r["bbox_mm"][5], 0)
    r = m(V2(rect(30, 10), ext(5, direction="both")))
    assert close(r["volume_mm3"], 3000) and close(r["bbox_mm"][2], -5) and close(r["bbox_mm"][5], 5)


@pytest.mark.parametrize("on, box", [
    ("top", [10, 0, -27, 40, 6, -7]),         # x = +X, y = -Z, n = +Y
    ("right", [0, 7, -40, 6, 27, -10]),       # x = -Z, y = +Y, n = +X
])
def test_named_plane_frames(on, box):
    r = m(V2(rect(30, 20, on=on, x=10, y=7), ext(6)))
    assert all(close(a, b) for a, b in zip(r["bbox_mm"], box)), r["bbox_mm"]


def test_tilted_plane_carries_the_sketch_frame():
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    r = m(V2(plane("t", origin=[5, 0, 0], normal=[0, -s, c], x_dir=[1, 0, 0]), rect(20, 10, on="t"), ext(4)))
    y_axis = (0, c, s)                        # n x x for n = (0, -s, c), x = (1, 0, 0)
    want = [5 + 10, 5 * y_axis[1] + 2 * -s, 5 * y_axis[2] + 2 * c]
    assert close(r["volume_mm3"], 800) and all(close(a, b) for a, b in zip(r["center_of_mass_mm"], want))


def test_cut_from_any_sketch_one_sided():
    r = m(V2(*BOX, plane("top20", base="front", offset=20), circle(5, on="top20", x=30, y=20), cut(8, direction="reverse")))
    assert close(r["volume_mm3"], 48000 - math.pi * 25 * 8)


# ---- revolve ----
def test_revolve_about_the_v_axis_and_partially():
    ring = math.pi * (20 ** 2 - 10 ** 2) * 5
    assert close(m(V2(rect(10, 5, x=10), {"$op": "feature.revolve", "id": "r", "args": {"axis": "v"}}))["volume_mm3"], ring)
    quarter = m(V2(rect(10, 5, x=10), {"$op": "feature.revolve", "id": "r", "args": {"axis": "v", "angle": 90}}))
    assert close(quarter["volume_mm3"], ring / 4)


def test_cut_revolve_groove():
    shaft = [{"$op": "sketch.profile", "id": "p", "sketched_on": "front", "args": {"points": [[0, 0], [60, 0], [60, 15], [0, 15]]}},
             {"$op": "feature.revolve", "id": "r", "args": {}}]
    groove = [rect(4, 3, id="g", x=28, y=12), {"$op": "feature.cut_revolve", "id": "cg", "args": {}}]
    r = m(V2(*shaft, *groove))
    assert close(r["volume_mm3"], math.pi * 225 * 60 - math.pi * (15 ** 2 - 12 ** 2) * 4)


def test_a_profile_across_the_axis_is_refused():
    with pytest.raises(SpecError, match="crosses the revolve axis"):
        build(V2(rect(10, 10, x=-5), {"$op": "feature.revolve", "id": "r", "args": {"axis": "v"}}))


# ---- mirror, patterns ----
HOLE = {"$op": "feature.hole", "id": "h", "sketched_on": "front", "args": {"diameter": 6, "through_all": True, "center": [15, 20]}}


def test_mirror_features_and_bodies():
    mid = plane("mid", origin=[30, 0, 0], normal=[1, 0, 0], x_dir=[0, 1, 0])
    r = m(V2(*BOX, HOLE, mid, {"$op": "feature.mirror", "id": "mh", "args": {"plane": "mid", "features": ["h"]}}))
    assert close(r["volume_mm3"], 48000 - 2 * math.pi * 9 * 20)
    r = m(V2(rect(30, 40), ext(20), {"$op": "feature.mirror", "id": "mb", "args": {"plane": "right", "body": True}}))
    assert close(r["volume_mm3"], 48000) and r["solids"] == 1 and close(r["bbox_mm"][0], -30)


def test_two_direction_and_any_axis_patterns():
    grid = {"$op": "feature.pattern.linear", "id": "g",
            "args": {"feature": "h", "count": 3, "spacing": 15, "direction": [1, 0, 0], "count2": 2, "spacing2": 10, "direction2": "Y"}}
    assert close(m(V2(*BOX, HOLE, grid))["volume_mm3"], 48000 - 6 * math.pi * 9 * 20)
    ring = {"$op": "feature.pattern.circular", "id": "c",
            "args": {"feature": "h", "count": 4, "axis": {"origin": [30, 20, 0], "direction": [0, 0, 1]}}}
    r = m(V2(*BOX, {**HOLE, "args": {**HOLE["args"], "center": [40, 20]}}, ring))
    assert close(r["volume_mm3"], 48000 - 4 * math.pi * 9 * 20)


# ---- holes ----
def stepped(kind, **extra):
    return {"$op": "feature.hole", "id": "h", "sketched_on": "top20",
            "args": {"diameter": 6, "depth": 15, "centers": [[20, 20], [40, 20]], "type": kind, "direction": "reverse", **extra}}


def test_counterbore_and_countersink():
    top = plane("top20", base="front", offset=20)
    cb = m(V2(*BOX, top, stepped("counterbore", cbore_diameter=11, cbore_depth=6)))
    assert close(cb["volume_mm3"], 48000 - 2 * (math.pi * 9 * 15 + math.pi * (5.5 ** 2 - 9) * 6))
    h = (12 - 6) / 2 / math.tan(math.radians(45))
    frustum = math.pi * h / 3 * (36 + 6 * 3 + 9)
    ck = m(V2(*BOX, top, stepped("countersink", csk_diameter=12)))
    assert close(ck["volume_mm3"], 48000 - 2 * (math.pi * 9 * 15 + frustum - math.pi * 9 * h))
    with pytest.raises(SpecError, match="needs direction normal or reverse"):
        build(V2(*BOX, top, {**stepped("counterbore", cbore_diameter=11, cbore_depth=6), "args": {
            **stepped("counterbore", cbore_diameter=11, cbore_depth=6)["args"], "direction": "both"}}))


# ---- shell, query fillets, booleans ----
def test_shell_open_at_the_top():
    shell = {"$op": "feature.shell", "id": "sh", "args": {"thickness": 2, "remove": {"faces": {
        "kind": "plane", "normal": "+Z", "at_extreme": {"axis": "Z", "which": "max"}, "expect_count": 1}}}}
    r = m(V2(*BOX, shell))
    assert close(r["volume_mm3"], 48000 - 56 * 36 * 18)


L_OUT = [[0, 0], [80, 0], [80, 10], [10, 10], [10, 60], [0, 60]]


def test_query_fillets_convex_and_concave():
    corners = {"$op": "feature.fillet", "id": "f", "args": {"radius": 5, "select": {"edges": {
        "kind": "line", "parallel_to": "Z", "expect_count": 4}}}}
    assert close(m(V2(*BOX, corners))["volume_mm3"], 48000 - 4 * 25 * (1 - math.pi / 4) * 20)
    lbr = [{"$op": "sketch.loops", "id": "s", "sketched_on": "front", "args": {"loops": [loop(L_OUT)]}}, ext(8)]
    inner = {"$op": "feature.fillet", "id": "f", "args": {"radius": 4, "select": {"edges": {
        "kind": "line", "parallel_to": "Z", "convex": False, "expect_count": 1}}}}
    assert close(m(V2(*lbr, inner))["volume_mm3"], 10400 + 16 * (1 - math.pi / 4) * 8)
    wrong = {"$op": "feature.fillet", "id": "f", "args": {"radius": 4, "select": {"edges": {
        "kind": "line", "parallel_to": "Z", "convex": True, "expect_count": 6}}}}
    with pytest.raises(QueryMismatch, match="found 5 edge"):
        build(V2(*lbr, wrong))


def test_bodies_and_booleans():
    b2 = [circle(10, id="cb", x=60, y=20), ext(20, id="eb", new_body="b2")]
    sub = m(V2(*BOX, *b2, {"$op": "body.boolean", "id": "x", "args": {"op": "subtract", "target": "main", "tools": ["b2"]}}))
    assert close(sub["volume_mm3"], 48000 - math.pi * 100 * 20 / 2)
    both = m(V2(*BOX, *b2, {"$op": "body.boolean", "id": "x", "args": {"op": "intersect", "target": "main", "tools": ["b2"]}}))
    assert close(both["volume_mm3"], math.pi * 100 * 20 / 2)
    two = build({**V2(*BOX, *b2), "expect": {"bodies": 2}})
    assert len(two.bodies) == 2


# ---- sweep and loft ----
def test_sweep_along_a_line_and_an_arc_is_pappus():
    k = 20 * (1 - math.cos(math.pi / 4))
    path = {"segments": [["L", [0, 0, 0], [0, 0, 50]],
                         ["A", [0, 0, 50], [k, 0, 50 + 20 * math.sin(math.pi / 4)], [20, 0, 70]]]}
    r = m(V2(circle(2), {"$op": "feature.sweep", "id": "sw", "args": {"profile": "c", "path": path}}))
    assert close(r["volume_mm3"], math.pi * 4 * (50 + 20 * math.pi / 2), rel=1e-7)


def test_a_spring_is_area_times_helix_length():
    t = (0.0, 2 * math.pi * 10, 5.0)
    ln = math.hypot(t[1], t[2])
    tilted = plane("hp", origin=[10, 0, 0], normal=[0, t[1] / ln, t[2] / ln], x_dir=[1, 0, 0])
    helix = {"helix": {"origin": [0, 0, 0], "axis": [0, 0, 1], "radius": 10, "pitch": 5, "turns": 3, "start_dir": [1, 0, 0]}}
    b = build(V2(tilted, circle(1, on="hp"), {"$op": "feature.sweep", "id": "sp", "args": {"profile": "c", "path": helix}}))
    assert b.tolerance_class == "F"
    assert close(measure(b.shape)["volume_mm3"], math.pi * 1 * 3 * ln, rel=1e-4)


def test_a_sweep_profile_must_face_the_path():
    path = {"segments": [["L", [0, 0, 0], [50, 0, 0]]]}
    with pytest.raises(SpecError, match="perpendicular to its start tangent"):
        build(V2(circle(2), {"$op": "feature.sweep", "id": "sw", "args": {"profile": "c", "path": path}}))


def test_ruled_lofts_are_prismatoids():
    hi = plane("z30", base="front", offset=30)
    sq = lambda a, id, on: {"$op": "sketch.loops", "id": id, "sketched_on": on,
                            "args": {"loops": [loop([[-a, -a], [a, -a], [a, a], [-a, a]])]}}
    r = m(V2(sq(10, "s1", "front"), hi, sq(5, "s2", "z30"), {"$op": "feature.loft", "id": "l", "args": {"sections": ["s1", "s2"]}}))
    assert close(r["volume_mm3"], 30 / 3 * (400 + 100 + 200))
    cones = m(V2(circle(10, id="c1"), hi, circle(5, id="c2", on="z30"),
                 {"$op": "feature.loft", "id": "l", "args": {"sections": ["c1", "c2"]}}))
    assert close(cones["volume_mm3"], math.pi * 30 / 3 * (100 + 50 + 25))
