"""Assemblies in OpenCascade (M2): a shaft, a bearing ring and a housing placed by frames; mates must hold, declared
contacts must touch without overlapping, anything else that overlaps is an interference."""
import copy

import pytest

pytest.importorskip("OCP")

from astracad_cad.step_check import read_step  # noqa: E402
from astracad_geom.assembly import build_assembly, export_step  # noqa: E402
from astracad_geom.occ_build import SpecError  # noqa: E402

AXIS = {"origin": [0, 0, 0], "z": [0, 0, 1], "x": [1, 0, 0]}


def circle(r, id):
    return {"$op": "sketch.circle", "id": id, "sketched_on": "front", "args": {"radius": r}}


def part(features, **interfaces):
    return {"spec": {"name": "p", "units": "mm", "language": 2, "features": features, "interfaces": interfaces}}


SHAFT = part([circle(10, "c"), {"$op": "feature.extrude", "id": "e", "args": {"depth": 100}}], axis=AXIS)
RING = part([{"$op": "sketch.loops", "id": "s", "sketched_on": "front",
              "args": {"loops": [[["C", 0, 0, 16]], [["C", 0, 0, 10]]]}},
             {"$op": "feature.extrude", "id": "e", "args": {"depth": 10}}], axis=AXIS)
HOUSING = part([{"$op": "sketch.rectangle", "id": "s", "sketched_on": "front", "args": {"width": 60, "height": 60, "x": -30, "y": -30}},
                {"$op": "feature.extrude", "id": "e", "args": {"depth": 20}},
                {"$op": "feature.hole", "id": "h", "sketched_on": "front", "args": {"diameter": 32, "through_all": True, "center": [0, 0]}}],
               bore_axis=AXIS)


def frame(z):
    return {"origin": [0, 0, z], "x": [1, 0, 0], "z": [0, 0, 1]}


ASM = {"assembly": "shaft_bearing_housing", "units": "mm",
       "parts": {"shaft": SHAFT, "ring": RING, "housing": HOUSING},
       "components": [{"id": "shaft", "part": "shaft", "frame": frame(0)},
                      {"id": "bearing", "part": "ring", "frame": frame(45)},
                      {"id": "housing", "part": "housing", "frame": frame(40)}],
       "mates": [{"type": "concentric", "a": ["shaft", "axis"], "b": ["bearing", "axis"]},
                 {"type": "coaxial", "a": ["bearing", "axis"], "b": ["housing", "bore_axis"]},
                 {"type": "plane_distance", "a": ["housing", "bore_axis"], "b": ["bearing", "axis"], "value": 5}],
       "contacts": [["shaft", "bearing"], ["bearing", "housing"]]}


def outcomes(r):
    return {c["name"]: c["outcome"] for c in r.checks}


def test_a_correct_assembly_passes_every_check(tmp_path):
    r = build_assembly(ASM)
    assert not r.failed, r.failed
    o = outcomes(r)
    assert o["contact:bearing~shaft"] == o["contact:bearing~housing"] == o["interference:all"] == "passed"
    assert sum(1 for k in o if k.startswith("mate:")) == 3
    export_step(r, tmp_path / "asm.step")
    assert read_step(tmp_path / "asm.step").solids == 3


def test_a_misplaced_housing_breaks_the_mate_and_the_contact():
    bad = copy.deepcopy(ASM)
    bad["components"][2]["frame"]["origin"] = [1, 0, 40]
    o = outcomes(build_assembly(bad))
    assert o["mate:coaxial:bearing.axis~housing.bore_axis"] == "failed"
    assert o["contact:bearing~housing"] == "failed" and o["interference:all"] == "failed"


def test_a_contact_that_does_not_touch_fails():
    loose = copy.deepcopy(ASM)
    loose["parts"]["ring"]["spec"]["features"][0]["args"]["loops"][1] = [["C", 0, 0, 11]]   # bore 22 on a 20 shaft
    o = outcomes(build_assembly(loose))
    assert o["contact:bearing~shaft"] == "failed"


def test_an_undeclared_overlap_is_an_interference():
    clash = copy.deepcopy(ASM)
    clash["contacts"] = [["shaft", "bearing"]]
    clash["parts"]["housing"]["spec"]["features"][2]["args"]["diameter"] = 30        # bore smaller than the ring
    o = outcomes(build_assembly(clash))
    assert o["interference:bearing~housing"] == "failed" and o["interference:all"] == "failed"


@pytest.mark.parametrize("mutate, words", [
    (lambda a: a["components"].append({"id": "shaft", "part": "shaft", "frame": frame(0)}), "used twice"),
    (lambda a: a["components"].append({"id": "x", "part": "nope", "frame": frame(0)}), "unknown part"),
    (lambda a: a["components"][0].update(frame={"origin": [0, 0, 0], "x": [1, 0, 0], "z": [1, 0, 0]}), "perpendicular"),
    (lambda a: a["mates"].append({"type": "concentric", "a": ["shaft", "nope"], "b": ["bearing", "axis"]}), "exist"),
])
def test_malformed_assemblies_are_refused(mutate, words):
    bad = copy.deepcopy(ASM)
    mutate(bad)
    with pytest.raises(SpecError, match=words):
        build_assembly(bad)
