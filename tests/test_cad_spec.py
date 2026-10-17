"""Spec validation for the revolve / pattern operations the runtime now implements."""
from astracad_cad.adapters.solidworks import MAC_KINDS, validate_spec

SHAFT = {"name": "stepped_shaft", "units": "mm", "features": [
    {"$op": "sketch.profile", "id": "prof", "sketched_on": "front",
     "args": {"points": [[0, 0], [60, 0], [60, 12.5], [40, 12.5], [40, 17.5], [0, 17.5]]}},
    {"$op": "feature.revolve", "id": "body", "args": {}}], "exports": [{"format": "step"}]}

PLATE = {"name": "plate_4holes", "units": "mm", "features": [
    {"$op": "sketch.rectangle", "id": "s", "sketched_on": "front", "args": {"width": 100, "height": 40}},
    {"$op": "feature.extrude", "id": "b", "args": {"depth": 10}},
    {"$op": "feature.hole", "id": "h", "args": {"diameter": 6, "depth": 10, "center": [20, 20]}},
    {"$op": "feature.pattern.linear", "id": "lp",
     "args": {"feature": "h", "count": 4, "spacing": 20, "direction": "X"}}]}


def test_new_kinds_are_accepted():
    assert {"sketch.profile", "feature.revolve", "feature.pattern.linear"} <= MAC_KINDS
    assert validate_spec(SHAFT) == [] and validate_spec(PLATE) == []


def test_non_positive_spacing_or_angle_is_rejected_before_cad():
    bad = {**PLATE, "features": PLATE["features"][:3] + [
        {"$op": "feature.pattern.linear", "id": "lp", "args": {"feature": "h", "count": 4, "spacing": 0}}]}
    assert any("spacing" in e for e in validate_spec(bad))
    bad = {**SHAFT, "features": [SHAFT["features"][0],
                                 {"$op": "feature.revolve", "id": "body", "args": {"angle": -90}}]}
    assert any("angle" in e for e in validate_spec(bad))


def test_still_unimplemented_kinds_are_refused():
    bad = {**PLATE, "features": PLATE["features"][:2] + [
        {"$op": "feature.shell", "id": "sh", "args": {"thickness": 2}}]}
    assert any("not implemented" in e for e in validate_spec(bad))
