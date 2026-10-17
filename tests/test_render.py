"""The preview page: what it draws, and what it has to admit it could not draw."""
from astracad_inspect.ingest.dxf import read_dxf
from astracad_inspect.plan import build_plan

from conftest import librecad_leader_doc


def test_the_preview_names_an_entity_it_cannot_draw_instead_of_failing(tmp_path):
    """The layout image comes from ezdxf's drawing frontend, which raises on the same vertex-less
    LEADER the bounding box tripped over. A drawing whose dimensions all read still has to produce
    a page - and say what is missing from it, because a preview that left an entity out is not the
    same claim as a preview of the drawing."""
    from astracad_inspect.export.render import render_drawing
    src = librecad_leader_doc(tmp_path)
    plan = build_plan(read_dxf(src))
    record = render_drawing(src, plan.layout, tmp_path / "page.png")
    assert record["entities_not_drawn"], record
    assert any("LEADER" in e for e in record["entities_not_drawn"]), record
    assert record["ink_fraction"] > 0.0, record
