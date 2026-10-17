"""General tolerances: applied only where a rule reaches, never assumed, never confused with a
tolerance printed on the dimension."""
import pytest

from astracad_inspect.general_tol import apply, detect, parse_user
from astracad_inspect.model import (CharKind, Characteristic, GeneralTolerance, NoTolerance,
                                    Symmetric)


def ch(kind, nominal, raw, tol=NoTolerance()):
    return Characteristic(cid="CH", kind=kind, raw_callout=raw, unit="mm", nominal=nominal, tolerance=tol)


@pytest.mark.parametrize("nominal,cls,pm", [
    (2, "m", 0.1), (5, "m", 0.1), (25, "m", 0.2), (100, "m", 0.3), (250, "m", 0.5),
    (800, "m", 0.8), (1500, "m", 1.2), (3000, "m", 2.0), (25, "f", 0.1), (25, "c", 0.5),
    (25, "v", 1.0), (6.0, "m", 0.1), (6.01, "m", 0.2),       # range edges: "over 3 up to 6"
])
def test_iso2768_linear_table(nominal, cls, pm):
    c = ch(CharKind.LINEAR, nominal, f"{nominal}")
    apply([c], parse_user(f"iso2768-{cls}"), "mm")
    assert c.tolerance == GeneralTolerance(pm, f"ISO 2768-{cls}")
    assert c.limits == (round(nominal - pm, 9), round(nominal + pm, 9))


@pytest.mark.parametrize("nominal,cls,pm", [(2, "m", 0.2), (5, "m", 0.5), (10, "m", 1.0), (10, "c", 2.0)])
def test_iso2768_radius_table(nominal, cls, pm):
    c = ch(CharKind.RADIUS, nominal, f"R{nominal}")
    apply([c], parse_user(f"iso2768-{cls}"), "mm")
    assert c.tolerance.plus_minus == pm


def test_refusals_are_recorded_not_guessed():
    rows = [ch(CharKind.LINEAR, 0.3, "0.3"),               # below the table
            ch(CharKind.LINEAR, 2.0, "2", Symmetric(0.05)),  # states its own tolerance
            ch(CharKind.ANGULAR, 45.0, "45°")]               # angle: needs a leg length
    counts = apply(rows, parse_user("iso2768-v"), "mm")
    assert rows[0].limits is None and counts["refused"] == 1
    assert rows[1].tolerance == Symmetric(0.05)
    assert isinstance(rows[2].tolerance, NoTolerance)
    assert any("shorter leg" in h.detail for h in rows[2].history)


def test_iso2768_is_not_applied_to_an_inch_drawing():
    c = ch(CharKind.LINEAR, 1.25, "1.250")
    assert apply([c], parse_user("iso2768-m"), "in")["refused"] == 1 and c.limits is None


def test_decimal_place_rules_from_title_block_text():
    spec = detect(["UNLESS OTHERWISE SPECIFIED:", "TWO PLACE DECIMAL ±0.01",
                   "THREE PLACE DECIMAL ±0.005", "ANGULAR: MACH ± BEND ±"])
    assert spec.places == {2: 0.01, 3: 0.005}
    a, b, c = (ch(CharKind.LINEAR, 1.25, "1.25"), ch(CharKind.LINEAR, 1.953, "1.953"),
               ch(CharKind.LINEAR, 2.0, "2.0"))
    counts = apply([a, b, c], spec, "in")
    assert a.limits == (1.24, 1.26) and b.limits == (1.948, 1.958)
    assert c.limits is None and counts == {"applied": 2, "refused": 1}   # no 1-place rule stated


def test_blank_template_lines_state_nothing():
    assert detect(["TWO PLACE DECIMAL ±", "THREE PLACE DECIMAL ±", "FRACTIONAL ±"]).empty


def test_iso_note_on_the_drawing_is_found():
    spec = detect(["GENERAL TOLERANCES TO ISO 2768-mK"])
    assert spec.iso_class == "m" and "2768" in spec.evidence


def test_bad_declaration_is_rejected():
    with pytest.raises(ValueError):
        parse_user("tight")


def test_statement_inside_a_title_block_insert_is_applied(tmp_path):
    """SOLIDWORKS writes title-block notes into block references, not modelspace text."""
    import ezdxf

    from astracad_inspect.ingest.dxf import read_dxf
    from astracad_inspect.pipeline import apply_general_tolerance
    doc = ezdxf.new("R2010", setup=True)
    for st in doc.dimstyles:
        st.dxf.dimlfac = 1.0
    doc.header["$INSUNITS"] = 4
    blk = doc.blocks.new("SW_NOTE_0")
    blk.add_mtext("GENERAL TOLERANCES: ISO 2768-mK")
    msp = doc.modelspace()
    msp.add_blockref("SW_NOTE_0", (200, 10))
    msp.add_line((0, 0), (50, 0))
    msp.add_linear_dim(base=(25, -8), p1=(0, 0), p2=(50, 0)).render()
    p = tmp_path / "tb.dxf"
    doc.saveas(p)
    b = read_dxf(p)
    counts = apply_general_tolerance(b)
    [c] = b.characteristics
    assert counts["applied"] == 1 and c.limits == (49.7, 50.3)        # 30-120 mm, class m: ±0.3
    assert "drawing text" in next(h.detail for h in c.history if h.actor == "general_tolerance")
