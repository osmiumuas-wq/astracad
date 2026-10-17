"""Write tests/fixtures/bracket_gdt_synthetic.dxf — SYNTHETIC test fixture, not a real part.

An 80 x 50 plate with a Ø12 H7 bore, two linear dimensions, one diameter dimension and five
feature control frames (TOLERANCE entities) covering: position at MMC with three datums, a
two-row composite (perpendicularity + parallelism), flatness without datums, profile with a
datum at MMC, and one deliberately malformed frame whose value box is not a number.

    py tools/make_gdt_fixture.py
"""
from __future__ import annotations

from pathlib import Path

import ezdxf

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "bracket_gdt_synthetic.dxf"
G = "{\\Fgdt;%s}"


def main() -> Path:
    doc = ezdxf.new("R2010", setup=True)
    # ezdxf's setup styles carry DIMLFAC 100 (drawn in m, shown in cm). These fixtures are
    # drawn 1:1 in mm and print their own values, so the factor must be 1 or the file
    # contradicts itself (a reader honouring DIMLFAC would see 100x every length).
    for _st in doc.dimstyles:
        _st.dxf.dimlfac = 1.0
    doc.header["$INSUNITS"] = 4                     # millimetres
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (80, 0), (80, 50), (0, 50)], close=True)
    msp.add_circle((40, 25), 6)
    msp.add_linear_dim(base=(40, -10), p1=(0, 0), p2=(80, 0), text="80 ±0.1").render()
    msp.add_linear_dim(base=(-10, 25), p1=(0, 0), p2=(0, 50), angle=90, text="50 ±0.1").render()
    msp.add_diameter_dim(center=(40, 25), radius=6, angle=45, text="%%c12 H7").render()
    frames = [
        ((52, 34), f"{G % 'j'}%%v{G % 'n'}0.05{G % 'm'}%%vA%%vB%%vC"),          # position MMC
        ((52, 44), f"{G % 'b'}%%v0.03%%vA^J{G % 'f'}%%v0.02%%vA"),               # two rows
        ((5, 56), f"{G % 'c'}%%v0.02"),                                           # flatness
        ((85, 10), f"{G % 'd'}%%v0.1%%vA{G % 'm'}"),                             # profile, datum MMC
        ((85, 40), f"{G % 'j'}%%vX%%vA"),                                         # malformed
    ]
    for (x, y), content in frames:
        msp.new_entity("TOLERANCE", {"content": content, "insert": (x, y), "dimstyle": "Standard"})
    msp.add_text("SYNTHETIC GD&T TEST PLATE", height=3).set_placement((0, -25))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(OUT)
    return OUT


if __name__ == "__main__":
    print(main())
