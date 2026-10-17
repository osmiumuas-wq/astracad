"""Generate labelled SYNTHETIC regression fixtures.

These drawings are machine-authored for testing. They are not real parts, and every file this
script writes is labelled synthetic so no result measured against them can be mistaken for a
result measured on production engineering data.

The ground-truth JSON records what the drawing was actually drawn to. Extraction accuracy is
then a comparison against that file, not against someone's reading of the picture.
"""
from __future__ import annotations

import json
from pathlib import Path

import ezdxf

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

SYNTHETIC_NOTE = "SYNTHETIC TEST FIXTURE - machine-generated, not a real part or a real drawing"


def _plate(doc, msp) -> None:
    """Outline: 100 x 50 plate with R10 corners, two 10 mm bores and one 20 mm counterbore."""
    msp.add_lwpolyline([(10, 0), (90, 0), (100, 10), (100, 40), (90, 50), (10, 50), (0, 40), (0, 10)],
                       close=True)
    msp.add_arc((10, 10), 10, 180, 270)
    msp.add_arc((90, 10), 10, 270, 360)
    msp.add_arc((90, 40), 10, 0, 90)
    msp.add_arc((10, 40), 10, 90, 180)
    msp.add_circle((25, 25), 5)
    msp.add_circle((75, 25), 5)
    msp.add_circle((50, 40), 10)
    msp.add_line((0, 0), (30, 30))          # a 45-degree edge for the angular dimension


def build_plate() -> tuple[Path, dict]:
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4            # millimetres, declared rather than assumed
    doc.header["$MEASUREMENT"] = 1
    msp = doc.modelspace()
    _plate(doc, msp)

    dims = []

    # 1. overall length, symmetric tolerance
    dims.append(msp.add_linear_dim(base=(50, 62), p1=(0, 0), p2=(100, 0),
                                   text="100 ±0.1"))
    # 2. overall height, unequal deviations
    dims.append(msp.add_linear_dim(base=(-12, 25), p1=(0, 0), p2=(0, 50), angle=90,
                                   text="50 +0.2/-0"))
    # 3. bore diameter with deviations
    dims.append(msp.add_diameter_dim(center=(25, 25), radius=5, angle=45,
                                     text="%%c10 +0.05/-0.02", dimstyle="EZ_RADIUS"))
    # 4. second bore diameter, limit pair, no stated nominal
    dims.append(msp.add_diameter_dim(center=(75, 25), radius=5, angle=135,
                                     text="%%c10.05/10.00", dimstyle="EZ_RADIUS"))
    # 5. corner radius
    dims.append(msp.add_radius_dim(center=(10, 40), mpoint=(10, 50), text="R10"))
    # 6. bore centre height, no tolerance stated on the callout
    dims.append(msp.add_linear_dim(base=(12, 12), p1=(25, 0), p2=(25, 25), angle=90,
                                   text="25"))
    # 7. an ISO 286-2 hole class: the callout states no numbers, the published table does
    dims.append(msp.add_diameter_dim(center=(50, 40), radius=10, angle=90,
                                     text="%%c20 H7", dimstyle="EZ_RADIUS"))
    # 8. angular
    dims.append(msp.add_angular_dim_3p(base=(20, 25), center=(0, 0), p1=(30, 0), p2=(21.2, 21.2),
                                       text="45°"))
    for d in dims:
        d.render()

    # 9. repeated-feature count on a diameter
    msp.add_diameter_dim(center=(50, 40), radius=10, angle=200, text="2X %%c20 THRU",
                         dimstyle="EZ_RADIUS").render()

    # 10. a fit pair: one callout naming a hole and a shaft. A characteristic is one feature, so
    # neither class may be adopted and the row has to block instead of guessing a side.
    msp.add_circle((50, 12), 5)
    msp.add_diameter_dim(center=(50, 12), radius=5, angle=90,
                         text="%%c10 H7/g6", dimstyle="EZ_RADIUS").render()

    # Free text that resembles dimensions. These must be escalated, never auto-accepted.
    msp.add_text("86.98 87.05", dxfattribs={"layer": "0", "height": 3}).set_placement((110, 55))
    msp.add_text("UNSPECIFIED EDGE BREAK", dxfattribs={"layer": "0", "height": 3}).set_placement((110, 48))

    # Title block content
    msp.add_text("Part name", dxfattribs={"height": 3}).set_placement((120, 5))
    msp.add_text("SYNTHETIC TEST PLATE", dxfattribs={"height": 3}).set_placement((120, 1))
    msp.add_text("Material", dxfattribs={"height": 3}).set_placement((140, 5))
    msp.add_text("Aluminium 6061-T6", dxfattribs={"height": 3}).set_placement((140, 1))

    FIXTURES.mkdir(parents=True, exist_ok=True)
    out = FIXTURES / "plate_synthetic.dxf"
    with open(out, "w", encoding="utf-8") as fh:
        doc.write(fh)
    return out, _plate_truth()


def _plate_truth() -> dict:
    """What the drawing above was actually drawn to, by construction."""
    return {
        "label": "plate_synthetic",
        "synthetic": True,
        "notice": SYNTHETIC_NOTE,
        "unit": "mm",
        "expected_characteristics": [
            {"expect": "callout", "raw_contains": "100 ±0.1", "kind": "linear",
             "nominal": 100.0, "tolerance": "symmetric:0.1", "resolved": True,
             "limits": [99.9, 100.1]},
            {"expect": "callout", "raw_contains": "50 +0.2/-0", "kind": "linear",
             "nominal": 50.0, "tolerance": "deviations:0.2:0.0", "resolved": True,
             "limits": [50.0, 50.2]},
            {"expect": "callout", "raw_contains": "%%c10 +0.05/-0.02", "kind": "diameter",
             "nominal": 10.0, "tolerance": "deviations:0.05:0.02", "resolved": True,
             "limits": [9.98, 10.05]},
            {"expect": "callout", "raw_contains": "%%c10.05/10.00", "kind": "diameter",
             "nominal": None, "tolerance": "limit_pair:10.0:10.05", "resolved": True,
             "limits": [10.0, 10.05]},
            {"expect": "callout", "raw_contains": "R10", "kind": "radius",
             "nominal": 10.0, "tolerance": "none", "resolved": True, "limits": None},
            {"expect": "callout", "raw_contains": "25", "kind": "linear",
             "nominal": 25.0, "tolerance": "none", "resolved": True, "limits": None},
            {"expect": "callout", "raw_contains": "%%c20 H7", "kind": "diameter",
             "nominal": 20.0, "tolerance": "fit_class:H7:20.0:20.021", "resolved": True,
             "limits": [20.0, 20.021]},
            {"expect": "callout", "raw_contains": "45°", "kind": "angular",
             "nominal": 45.0, "tolerance": "none", "resolved": True, "limits": None},
            {"expect": "callout", "raw_contains": "2X %%c20 THRU", "kind": "diameter",
             "nominal": 20.0, "tolerance": "none", "count": 2, "resolved": True, "limits": None},
            {"expect": "callout", "raw_contains": "%%c10 H7/g6", "kind": "diameter",
             "nominal": 10.0, "tolerance": "fit_class:H7/g6:none", "resolved": False,
             "limits": None, "must_block": True},
        ],
        # Loose text that looks dimensional: the engine must escalate it, not extract it.
        "expected_uninterpreted": [
            {"text_contains": "86.98 87.05"},
        ],
        "expected_minimum_characteristic_count": 10,
    }


def build_broken() -> tuple[Path, dict]:
    """A drawing whose callouts disagree with its geometry, to prove the cross-check bites.

    The length is drawn at 100 but dimensioned 90 ±0.1 — a 10 mm contradiction that a
    text-only parser would report as clean. The height is drawn and dimensioned the same, so
    the neighbour dimension must still pass: the block has to be specific, not blanket.
    """
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 20), (0, 20)], close=True)
    msp.add_linear_dim(base=(50, 28), p1=(0, 0), p2=(100, 0), text="90 ±0.1").render()
    msp.add_linear_dim(base=(108, 10), p1=(100, 0), p2=(100, 20), angle=90,
                       text="20 ±0.1").render()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    out = FIXTURES / "contradiction_synthetic.dxf"
    with open(out, "w", encoding="utf-8") as fh:
        doc.write(fh)
    return out, {
        "label": "contradiction_synthetic",
        "synthetic": True,
        "notice": SYNTHETIC_NOTE,
        "unit": "mm",
        # By construction: the outline is 100 x 20. The first callout says 90, so it cannot be
        # satisfied by any geometry in this drawing.
        "drawn_extents": [100.0, 20.0],
        "expected_characteristics": [
            {"expect": "callout", "raw_contains": "90 ±0.1", "kind": "linear",
             "nominal": 90.0, "tolerance": "symmetric:0.1", "resolved": True,
             "limits": [89.9, 90.1],
             "drawn_value": 100.0, "must_block": True},
            {"expect": "callout", "raw_contains": "20 ±0.1", "kind": "linear",
             "nominal": 20.0, "tolerance": "symmetric:0.1", "resolved": True,
             "limits": [19.9, 20.1],
             "drawn_value": 20.0, "must_block": False},
        ],
        "expected_uninterpreted": [],
        "expect_cross_check_failure_for": "90 ±0.1",
        "expect_cross_check_pass_for": "20 ±0.1",
    }


if __name__ == "__main__":
    written = []
    for builder in (build_plate, build_broken):
        path, truth = builder()
        tpath = path.with_name(path.stem + ".expected.json")
        tpath.write_text(json.dumps(truth, indent=2), encoding="utf-8")
        written += [path, tpath]
    for w in written:
        print("wrote", w)