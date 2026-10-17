import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def label(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))


def dxf(name: str) -> Path:
    return FIXTURES / f"{name}.dxf"


# Verbatim AcDbLeader tag list from _sources/corpus/dim_sample.dxf (LibreCAD, handle E5, sha256
# 4d8c69d226939bbe): a dimension-styled leader with annotation type 3 and no group-10 vertices.
LEADER_WITHOUT_VERTICES = """  0
LEADER
  5
E5
100
AcDbEntity
  8
0
100
AcDbLeader
  3
Standard
 71
   1
 72
   0
 73
   3
 74
   0
 75
   0
 40
 1
 41
 10
 76
   0
"""


def librecad_leader_doc(tmp_path: Path) -> Path:
    """A measurable drawing plus one real-world malformed entity.

    LibreCAD writes LEADERs that carry no vertex coordinates, and ezdxf then raises wherever it has
    to disassemble one (bounding box, preview render). The entity is spliced in as raw tags because
    a vertex-less leader created in memory is dropped when the file is saved - which is exactly how
    this defect stays invisible until a real drawing opens."""
    import ezdxf
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_line((0, 0), (40, 0))
    msp.add_line((0, 0), (0, 20))
    msp.add_linear_dim(base=(20, 8), p1=(0, 0), p2=(40, 0), text="40").render()
    text = io.StringIO()
    doc.write(text)
    body = text.getvalue()
    start = body.index("  2\nENTITIES")
    cut = body.index("  0\nENDSEC", start)
    path = tmp_path / "librecad_leader.dxf"
    path.write_text(body[:cut] + LEADER_WITHOUT_VERTICES + body[cut:], encoding="utf-8")
    return path


def shown_text_dim_doc(tmp_path: Path, *, length: float = 42.4264, shown: str | None = "42,43",
                       override: str = "",
                       points: tuple[tuple[float, float], tuple[float, float]] | None = None,
                       measurement: str = "horizontal", name: str = "shown_text") -> Path:
    """A drawing that states its size where CAD authors actually state it: in the text rendered
    inside the dimension's own block, with nothing (or only '<>') on the modelspace entity.

    `shown` is rewritten into the block's text entity so the fixture says exactly what a real file
    says, and `None` strips the text entities out (a dimension that states nothing anywhere).
    `points` and `measurement` together let the declared projection disagree with the printed
    number, which is how the LibreCAD sample behaves on two of its dimensions."""
    import ezdxf
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    p1, p2 = points if points else ((0, 0), (length, 0))
    msp.add_line(p1, p2)
    msp.add_line(p1, (p1[0], p1[1] + 20))
    msp.add_linear_dim(base=((p1[0] + p2[0]) / 2, max(p1[1], p2[1]) + 8),
                       p1=p1, p2=p2, text=override).render()
    dim = next(iter(msp.query("DIMENSION")))
    if measurement == "horizontal":
        dim.dxf.dimtype = 0
        dim.dxf.angle = 0.0
    elif measurement == "aligned":
        dim.dxf.dimtype = 1
    block = doc.blocks.get(dim.dxf.geometry)
    for e in list(block):
        if e.dxftype() in ("MTEXT", "TEXT", "ATTRIB"):
            if shown is None:
                block.delete_entity(e)
            else:
                e.dxf.text = shown
    path = tmp_path / f"{name}.dxf"
    doc.saveas(str(path))
    return path


@pytest.fixture
def bundle_factory():
    """A fresh bundle per test: building a plan mutates its characteristics."""
    from astracad_inspect.ingest.dxf import read_dxf

    def make(name="plate_synthetic", **kw):
        return read_dxf(dxf(name), **kw)
    return make


@pytest.fixture
def plan_factory(bundle_factory):
    from astracad_inspect.plan import build_plan

    def make(name="plate_synthetic", **kw):
        return build_plan(bundle_factory(name), **kw)
    return make


@pytest.fixture
def plate(bundle_factory):
    return bundle_factory("plate_synthetic")


@pytest.fixture
def plate_plan(plan_factory):
    return plan_factory("plate_synthetic")


@pytest.fixture
def contradiction_plan(plan_factory):
    return plan_factory("contradiction_synthetic")
