"""Drawing -> CAD: a part is rebuilt from its views only when the views prove it is one constant
extrusion with through openings; every other drawing is refused with the reason, never approximated."""
import math
import shutil
from pathlib import Path

import ezdxf
import pytest

from astracad_core.drawing_to_cad import reconstruct

FIX = Path(__file__).parent / "fixtures"
AREA = 100 * 60 - (4 - math.pi) * 25 - 2 * math.pi * 9           # R5 corners, two dia 6 holes
HOLES = [(30, 30), (70, 30)]


def plate_dxf(path: Path, *, s: float = 1.0, hidden: bool = True, blind: bool = False, step: bool = False,
              dims: bool = True, extra_view: bool = False, wrong_text: str | None = None) -> Path:
    """Third-angle views of a 100 x 60 x 10 plate (R5 corners, two dia 6 holes), drawn at scale s."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    m = doc.modelspace()
    L = lambda x1, y1, x2, y2, lt="CONTINUOUS": m.add_line((x1 * s, y1 * s), (x2 * s, y2 * s), dxfattribs={"linetype": lt})  # noqa: E731
    # front view: the outline (lines + R5 arcs) and the holes
    L(5, 0, 95, 0); L(100, 5, 100, 55); L(95, 60, 5, 60); L(0, 55, 0, 5)
    for cx, cy, a0 in ((95, 5, 270), (95, 55, 0), (5, 55, 90), (5, 5, 180)):
        m.add_arc((cx * s, cy * s), 5 * s, a0, a0 + 90)
    for cx, cy in HOLES:
        m.add_circle((cx * s, cy * s), 3 * s)
    # top view (above): x shared with the front view, depth 10 upwards
    y0 = 80
    L(0, y0, 100, y0); L(100, y0, 100, y0 + 10); L(100, y0 + 10, 0, y0 + 10); L(0, y0 + 10, 0, y0)
    for x in (5, 95):
        L(x, y0, x, y0 + 10)                                       # tangent edges of the corner rounds
    for cx, _ in HOLES:
        for x in (cx - 3, cx + 3):
            if hidden:
                L(x, y0 + (5 if blind else 0), x, y0 + 10, "HIDDEN")
    # side view (right): y shared, depth 10 to the right
    x0 = 120
    if step:                                                         # an L-shaped side: not one extrusion
        L(x0, 0, x0 + 10, 0); L(x0 + 10, 0, x0 + 10, 30); L(x0 + 10, 30, x0 + 5, 30); L(x0 + 5, 30, x0 + 5, 60)
        L(x0 + 5, 60, x0, 60); L(x0, 60, x0, 0)
    else:
        L(x0, 0, x0 + 10, 0); L(x0 + 10, 0, x0 + 10, 60); L(x0 + 10, 60, x0, 60); L(x0, 60, x0, 0)
        for y in (5, 55):
            L(x0, y, x0 + 10, y)
        if hidden:
            for y in (27, 33):
                L(x0 + (5 if blind else 0), y, x0 + 10, y, "HIDDEN")
    if extra_view:
        m.add_circle((300 * s, 300 * s), 8 * s)                     # a detail nobody explains
    if dims:
        m.add_linear_dim(base=(50 * s, -15 * s), p1=(0, 0), p2=(100 * s, 0), text=wrong_text or "<>",
                         override={"dimlfac": 1 / s}).render()
        m.add_linear_dim(base=(-15 * s, 30 * s), p1=(0, 5 * s), p2=(0, 55 * s), angle=90,
                         override={"dimlfac": 1 / s}).render()
        m.add_linear_dim(base=(50 * s, 100 * s), p1=(0, 80 * s), p2=(0, 90 * s), angle=90,
                         override={"dimlfac": 1 / s}).render()
        m.add_linear_dim(base=(125 * s, -15 * s), p1=(120 * s, 0), p2=(130 * s, 0),
                         override={"dimlfac": 1 / s}).render()
    doc.saveas(path)
    return path


def test_plate_is_rebuilt_exactly_from_its_views(tmp_path):
    it = reconstruct(plate_dxf(tmp_path / "plate.dxf"))
    assert it.executable, (it.missing, it.ambiguities)
    (section, extrude) = it.spec["features"]
    loops = section["args"]["loops"]
    assert len(loops) == 3 and sum(1 for s in loops[0] if s[0] == "A") == 4 and [l[0][0] for l in loops[1:]] == ["C", "C"]
    assert extrude["args"]["depth"] == pytest.approx(10)
    assert it.expect["volume_mm3"] == pytest.approx(AREA * 10)
    assert it.expect["size_mm"] == [100, 60, 10]
    assert it.fact("openings_through") is True


def test_scale_comes_from_the_dimensions(tmp_path):
    it = reconstruct(plate_dxf(tmp_path / "plate_2to1.dxf", s=2.0))
    assert it.executable and it.fact("view_scale_factor") == pytest.approx(0.5)
    assert it.expect["volume_mm3"] == pytest.approx(AREA * 10) and it.expect["size_mm"] == [100, 60, 10]


def test_blind_openings_and_steps_are_refused(tmp_path):
    blind = reconstruct(plate_dxf(tmp_path / "blind.dxf", blind=True))
    assert not blind.executable and "full depth" in blind.ambiguities[0]
    step = reconstruct(plate_dxf(tmp_path / "step.dxf", step=True))
    assert not step.executable and "not a rectangle" in step.ambiguities[0]


def test_through_must_be_shown_or_stated(tmp_path):
    p = plate_dxf(tmp_path / "no_hidden.dxf", hidden=False)
    it = reconstruct(p)
    assert not it.executable and "no hidden lines" in it.ambiguities[0]
    ok = reconstruct(p, through=True)
    assert ok.executable and next(f for f in ok.facts if f.name == "openings_through").provenance == "USER_SPECIFIED"


def test_every_view_must_be_explained(tmp_path):
    it = reconstruct(plate_dxf(tmp_path / "extra.dxf", extra_view=True))
    assert not it.executable and "not aligned with the outline view" in it.ambiguities[0]


def test_scale_is_never_assumed(tmp_path):
    p = plate_dxf(tmp_path / "nodims.dxf", dims=False)
    it = reconstruct(p)
    assert not it.executable and "no dimension giving its scale" in it.ambiguities[0]
    stated = reconstruct(p, scale=1.0)
    assert stated.executable and stated.expect["volume_mm3"] == pytest.approx(AREA * 10)


def test_a_dimension_contradicting_its_geometry_stops_the_rebuild(tmp_path):
    it = reconstruct(plate_dxf(tmp_path / "liar.dxf", wrong_text="90"))
    assert not it.executable and "not to scale" in it.ambiguities[0]


def test_real_solidworks_drawing_rebuilds_the_measured_volume(tmp_path):
    """plate_r5_hlv.dxf was made by `mac drawing --hidden-lines` from a part SOLIDWORKS measured at
    58654.424808 mm^3 (see fixtures/sw_drawing/PROVENANCE.md); the rebuild must predict exactly that."""
    p = shutil.copy(FIX / "sw_drawing" / "plate_r5_hlv.dxf", tmp_path / "plate_r5_hlv.dxf")
    it = reconstruct(p)
    assert it.executable, (it.missing, it.ambiguities)
    assert it.expect["volume_mm3"] == pytest.approx(58654.424808, abs=1e-6)
    assert it.expect["size_mm"] == pytest.approx([100, 60, 10])


def single_view_plate(path: Path, *, note: str | None = "THK 4", scale_note: str | None = None,
                      s: float = 1.0, dims: bool = True) -> Path:
    """One view of a 50 x 30 gasket with a dia 8 hole - the usual drawing of a flat part."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    m = doc.modelspace()
    for a, b in (((0, 0), (50, 0)), ((50, 0), (50, 30)), ((50, 30), (0, 30)), ((0, 30), (0, 0))):
        m.add_line((a[0] * s, a[1] * s), (b[0] * s, b[1] * s))
    m.add_circle((25 * s, 15 * s), 4 * s)
    for t in (note, scale_note):
        if t:
            m.add_text(t, dxfattribs={"insert": (0, -30 * s), "height": 3})
    if dims:
        m.add_linear_dim(base=(25 * s, -10 * s), p1=(0, 0), p2=(50 * s, 0), override={"dimlfac": 1 / s}).render()
    doc.saveas(path)
    return path


def test_a_single_view_flat_part_takes_its_thickness_from_the_note_or_the_user(tmp_path):
    gasket = single_view_plate(tmp_path / "gasket.dxf")
    asked = reconstruct(gasket)                       # one view cannot show the hole's depth: asked, not assumed
    assert not asked.executable and "no hidden lines" in asked.ambiguities[0]
    it = reconstruct(gasket, through=True)
    assert it.executable, (it.missing, it.ambiguities)
    f = next(f for f in it.facts if f.name == "thickness_mm")
    assert f.value == pytest.approx(4) and f.provenance == "USER_SPECIFIED" and "THK 4" in f.source_text
    assert it.expect["volume_mm3"] == pytest.approx((50 * 30 - math.pi * 16) * 4)
    bare = single_view_plate(tmp_path / "bare.dxf", note=None)
    assert not reconstruct(bare).executable and "thickness" in reconstruct(bare).ambiguities[0]
    assert reconstruct(bare, thickness=2.5, through=True).expect["volume_mm3"] == pytest.approx((50 * 30 - math.pi * 16) * 2.5)


def test_a_thickness_note_that_contradicts_the_views_stops_the_rebuild(tmp_path):
    p = plate_dxf(tmp_path / "plate.dxf")
    it = reconstruct(p, thickness=12)
    assert not it.executable and "views show a thickness of 10 mm" in it.ambiguities[0]


def test_the_scale_written_on_the_sheet_is_used_only_without_contrary_dimensions(tmp_path):
    p = single_view_plate(tmp_path / "half.dxf", scale_note="SCALE: 1:2", s=0.5, dims=False)
    it = reconstruct(p, through=True)
    assert it.executable and it.fact("sheet_scale") == "SCALE: 1:2"
    assert it.expect["size_mm"] == pytest.approx([50, 30, 4])
    wrong = single_view_plate(tmp_path / "wrong.dxf", scale_note="SCALE: 1:5", s=0.5, dims=True)
    it2 = reconstruct(wrong, through=True)        # its dimension says 2:1 down, the note 1:5: the dimension wins
    assert it2.executable and it2.fact("sheet_scale") is None and it2.expect["size_mm"] == pytest.approx([50, 30, 4])


def test_a_single_views_outline_is_never_taken_for_a_sheet_frame(tmp_path):
    """Found 2026-10-18: the outline of a one-view drawing spans the whole drawing, like a frame; it was
    dropped and only the hole was rebuilt (an 8 mm disc). A dimensioned outline is part geometry."""
    it = reconstruct(single_view_plate(tmp_path / "gasket.dxf"), through=True)
    assert it.expect["size_mm"] == pytest.approx([50, 30, 4]) and it.fact("sheet_frame") is None


def turned_part_dxf(path: Path, *, kind: str = "stepped", s: float = 1.0, dims: bool = True,
                    end_view: bool = True, dia_dims: bool = False, chamfer: float = 0.0,
                    asymmetric: bool = False, wrong_text: str | None = None) -> Path:
    """Drawings of turned parts: stepped shaft, chamfered cylinder, or hollow tube/bushing."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    if kind == "stepped":
        L1, R1, L2, R2 = 25.0 * s, 10.0 * s, 35.0 * s, 15.0 * s
        Lt = L1 + L2
        msp.add_line((-5 * s, 0), (Lt + 5 * s, 0), dxfattribs={"linetype": "CENTER"})
        # Top
        msp.add_line((0, R1), (L1, R1))
        msp.add_line((L1, R1), (L1, R2))
        msp.add_line((L1, R2), (Lt, R2))
        # Bottom
        R1_bot = R1 if not asymmetric else R1 * 1.5
        msp.add_line((0, -R1_bot), (L1, -R1_bot))
        msp.add_line((L1, -R1_bot), (L1, -R2))
        msp.add_line((L1, -R2), (Lt, -R2))
        # Ends
        msp.add_line((0, -R1_bot), (0, R1))
        msp.add_line((Lt, -R2), (Lt, R2))
        if end_view:
            cx = Lt + 40 * s
            msp.add_circle((cx, 0), R1)
            msp.add_circle((cx, 0), R2)
        if dims:
            msp.add_linear_dim(base=(L1 / 2, -25 * s), p1=(0, 0), p2=(L1, 0),
                              override={"dimlfac": 1 / s}).render()
            msp.add_linear_dim(base=(L1 + L2 / 2, -25 * s), p1=(L1, 0), p2=(Lt, 0),
                              override={"dimlfac": 1 / s}).render()
            if dia_dims:
                msp.add_linear_dim(base=(-15 * s, 0), p1=(0, -R1), p2=(0, R1), angle=90,
                                  text=wrong_text or "%%c<>", override={"dimlfac": 1 / s}).render()
                msp.add_linear_dim(base=(Lt + 15 * s, 0), p1=(Lt, -R2), p2=(Lt, R2), angle=90,
                                  text="%%c<>", override={"dimlfac": 1 / s}).render()
    elif kind == "chamfered":
        L, R, c = 50.0 * s, 10.0 * s, chamfer * s
        msp.add_line((-5 * s, 0), (L + 5 * s, 0), dxfattribs={"linetype": "CENTER"})
        # Top
        msp.add_line((c, R), (L - c, R))
        msp.add_line((0, R - c), (c, R))
        msp.add_line((L - c, R), (L, R - c))
        # Bottom
        msp.add_line((c, -R), (L - c, -R))
        msp.add_line((0, -(R - c)), (c, -R))
        msp.add_line((L - c, -R), (L, -(R - c)))
        # Ends
        msp.add_line((0, -(R - c)), (0, R - c))
        msp.add_line((L, -(R - c)), (L, R - c))
        if end_view:
            cx = L + 40 * s
            msp.add_circle((cx, 0), R)
        if dims:
            msp.add_linear_dim(base=(L / 2, -25 * s), p1=(0, 0), p2=(L, 0),
                              override={"dimlfac": 1 / s}).render()
            if dia_dims:
                msp.add_linear_dim(base=(-15 * s, 0), p1=(0, -R), p2=(0, R), angle=90,
                                  text="%%c<>", override={"dimlfac": 1 / s}).render()
    elif kind == "tube":
        L, Ro, Ri = 50.0 * s, 20.0 * s, 10.0 * s
        msp.add_line((-5 * s, 0), (L + 5 * s, 0), dxfattribs={"linetype": "CENTER"})
        # Outer
        msp.add_line((0, Ro), (L, Ro))
        msp.add_line((0, -Ro), (L, -Ro))
        # Inner (hidden)
        msp.add_line((0, Ri), (L, Ri), dxfattribs={"linetype": "HIDDEN"})
        msp.add_line((0, -Ri), (L, -Ri), dxfattribs={"linetype": "HIDDEN"})
        # Ends
        msp.add_line((0, Ri), (0, Ro))
        msp.add_line((0, -Ro), (0, -Ri))
        msp.add_line((L, Ri), (L, Ro))
        msp.add_line((L, -Ro), (L, -Ri))
        if end_view:
            cx = L + 40 * s
            msp.add_circle((cx, 0), Ri)
            msp.add_circle((cx, 0), Ro)
        if dims:
            msp.add_linear_dim(base=(L / 2, -35 * s), p1=(0, 0), p2=(L, 0),
                              override={"dimlfac": 1 / s}).render()
            if dia_dims:
                msp.add_linear_dim(base=(-15 * s, 0), p1=(0, -Ro), p2=(0, Ro), angle=90,
                                  text="%%c<>", override={"dimlfac": 1 / s}).render()
    doc.saveas(path)
    return path


def test_stepped_shaft_two_views_rebuilds_exact_revolve_profile_and_volume(tmp_path):
    p = turned_part_dxf(tmp_path / "stepped_2v.dxf", kind="stepped", end_view=True)
    it = reconstruct(p)
    assert it.executable, (it.missing, it.ambiguities)
    assert it.expect["size_mm"] == [30, 30, 60]
    assert it.expect["volume_mm3"] == pytest.approx(math.pi * 10**2 * 25 + math.pi * 15**2 * 35)
    features = it.spec["features"]
    assert [f["$op"] for f in features] == ["sketch.profile", "feature.revolve"]
    assert features[0]["args"]["points"] == [[0.0, 0.0], [0.0, 10.0], [25.0, 10.0], [25.0, 15.0], [60.0, 15.0], [60.0, 0.0]]


def test_single_view_stepped_shaft_with_diameter_dimensions(tmp_path):
    p = turned_part_dxf(tmp_path / "stepped_1v.dxf", kind="stepped", end_view=False, dia_dims=True)
    it = reconstruct(p)
    assert it.executable, (it.missing, it.ambiguities)
    assert it.expect["size_mm"] == [30, 30, 60]
    assert it.expect["volume_mm3"] == pytest.approx(math.pi * 10**2 * 25 + math.pi * 15**2 * 35)


def test_turned_part_with_chamfered_ends(tmp_path):
    p = turned_part_dxf(tmp_path / "chamfered.dxf", kind="chamfered", chamfer=1.0, end_view=True)
    it = reconstruct(p)
    assert it.executable, (it.missing, it.ambiguities)
    assert it.expect["size_mm"] == [20, 20, 50]
    assert it.expect["volume_mm3"] == pytest.approx(math.pi * 10**2 * 50 - 2 * math.pi * 1**2 * (10 - 1/3))
    pts = it.spec["features"][0]["args"]["points"]
    assert pts == [[0.0, 0.0], [0.0, 9.0], [1.0, 10.0], [49.0, 10.0], [50.0, 9.0], [50.0, 0.0]]


def test_hollow_bushing_with_hidden_lines(tmp_path):
    p = turned_part_dxf(tmp_path / "bushing.dxf", kind="tube", end_view=True)
    it = reconstruct(p)
    assert it.executable, (it.missing, it.ambiguities)
    assert it.expect["size_mm"] == [40, 40, 50]
    assert it.expect["volume_mm3"] == pytest.approx(math.pi * (20**2 - 10**2) * 50)
    pts = it.spec["features"][0]["args"]["points"]
    assert pts == [[0.0, 10.0], [0.0, 20.0], [50.0, 20.0], [50.0, 10.0]]


def test_single_view_without_diameter_proof_is_refused(tmp_path):
    p = turned_part_dxf(tmp_path / "no_dia.dxf", kind="stepped", end_view=False, dia_dims=False)
    it = reconstruct(p)
    assert not it.executable
    assert any("not assumed to be a shaft" in a for a in it.ambiguities)


def test_asymmetric_turned_view_is_refused(tmp_path):
    p = turned_part_dxf(tmp_path / "asymm.dxf", kind="stepped", asymmetric=True, end_view=True)
    it = reconstruct(p)
    assert not it.executable
    assert any("not symmetric across its centreline" in a for a in it.ambiguities)


def test_stepped_shaft_scale_comes_from_dimensions(tmp_path):
    p = turned_part_dxf(tmp_path / "scaled.dxf", kind="stepped", s=0.5, end_view=True)
    it = reconstruct(p)
    assert it.executable, (it.missing, it.ambiguities)
    assert it.fact("view_scale_factor") == pytest.approx(2.0)
    assert it.expect["size_mm"] == [30, 30, 60]
    assert it.expect["volume_mm3"] == pytest.approx(math.pi * 10**2 * 25 + math.pi * 15**2 * 35)

