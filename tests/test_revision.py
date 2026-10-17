from pathlib import Path

import ezdxf
import pytest
from openpyxl import load_workbook

from astracad_inspect.revision import compare_drawings, compare_plans, write_changeset
from conftest import dxf


def _dims(doc):
    return {d.dxf.text: d for d in doc.modelspace().query("DIMENSION")}


def make_rev_b(tmp_path: Path, shift=(0.0, 0.0)) -> Path:
    doc = ezdxf.readfile(dxf("plate_synthetic"))
    dims = _dims(doc)
    msp = doc.modelspace()
    dims["100 ±0.1"].dxf.text = "100 ±0.05"                 # tolerance tightened
    dims["25"].dxf.text = "25 ±0.2"                         # tolerance added
    msp.delete_entity(dims["45°"])                          # angular removed
    msp.add_linear_dim(base=(50, -12), p1=(25, 0), p2=(75, 0), text="50 ±0.1").render()  # added
    for txt in list(msp.query("TEXT MTEXT")):
        if "Aluminium" in (txt.dxf.get("text", "") if txt.dxftype() == "TEXT" else txt.text):
            pass
    if shift != (0.0, 0.0):
        m = ezdxf.math.Matrix44.translate(shift[0], shift[1], 0)
        for e in msp:
            try:
                e.transform(m)
            except Exception:
                pass
    p = tmp_path / "plate_rev_b.dxf"
    doc.saveas(p)
    return p


@pytest.mark.parametrize("shift", [(0.0, 0.0), (300.0, -120.0)])
def test_revision_changes_detected_independent_of_position(tmp_path, shift):
    b = make_rev_b(tmp_path, shift)
    cs = compare_drawings(dxf("plate_synthetic"), b, old_revision="A", new_revision="B")
    s = cs.summary()
    assert s.get("REMOVED") == 1 and s.get("ADDED") == 1, cs.to_dict()["summary"]
    assert s.get("CHANGED_TOLERANCE") == 2
    removed = next(c for c in cs.changes if c.status == "REMOVED")
    assert removed.kind == "angular"
    tightened = next(c for c in cs.changes if c.status == "CHANGED_TOLERANCE"
                     and c.old["callout"] == "100 ±0.1")
    assert tightened.new["limits"] == [99.95, 100.05] and tightened.requires_review
    unchanged = s.get("UNCHANGED", 0) + s.get("MOVED", 0)
    assert unchanged == 10 - 1 - 2           # 10 old features, 1 removed, 2 changed
    assert cs.needs_review


def test_identical_revisions_have_no_consequential_change():
    cs = compare_drawings(dxf("plate_synthetic"), dxf("plate_synthetic"))
    assert set(cs.summary()) == {"UNCHANGED"} and not cs.needs_review


def test_ambiguous_pair_is_not_guessed():
    def ch(cid, x):
        return {"cid": cid, "kind": "diameter", "raw_callout": "%%c10", "nominal": 10,
                "tolerance": None, "limits": None, "count": 1, "geometry_value": 10,
                "source": {"definition_points": [[x, 0]], "anchor_x": x, "anchor_y": 0}}
    old = {"extents": [0, 0, 100, 0.0001], "characteristics": [ch("A", 50)]}
    new = {"extents": [0, 0, 100, 0.0001], "characteristics": [ch("B", 47), ch("C", 53)]}
    cs = compare_plans(old, new)
    assert cs.summary().get("AMBIGUOUS") == 1 and cs.summary().get("ADDED") == 2


def test_changeset_exports_reopen(tmp_path):
    cs = compare_drawings(dxf("plate_synthetic"), make_rev_b(tmp_path))
    out = write_changeset(cs, tmp_path / "o", "plate_A_to_B")
    wb = load_workbook(out["xlsx"])
    rows = list(wb["Changes"].iter_rows(min_row=2, values_only=True))
    assert len(rows) == len(cs.changes)
    assert sum(1 for r in rows if r[1] == "REQUIRED") == sum(c.requires_review for c in cs.changes)
    assert "NOT APPROVED" in str(list(wb["Provenance"].values))


def test_plan_from_disk_equals_plan_in_memory(tmp_path):
    import json
    from astracad_inspect.ingest.dxf import read_dxf
    from astracad_inspect.plan import build_plan
    mem = build_plan(read_dxf(dxf("plate_synthetic"), None, None)).to_dict()
    disk = json.loads(json.dumps(mem, default=str))
    assert set(compare_plans(disk, mem).summary()) == {"UNCHANGED"}
