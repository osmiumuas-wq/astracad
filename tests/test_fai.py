"""AS9102-structured FAI workbook and drawing zones."""
import json

from astracad_inspect.export.fai import TO_COMPLETE, verify_fai, write_fai
from astracad_inspect.model import CharKind, Characteristic, SourceLocation
from astracad_inspect.zones import assign_zones


def _char(x, y, page=1):
    return Characteristic(cid="CH", kind=CharKind.LINEAR, raw_callout="10", unit="mm", nominal=10.0,
                          source=SourceLocation("d", "s", "A", page, "h", "t", "", x, y))


def test_zone_is_nearest_column_and_row_label():
    labels = [(1, str(n), x, 5) for n, x in ((4, 50), (3, 150), (2, 250), (1, 350))] + \
             [(1, r, 5, y) for r, y in (("A", 30), ("B", 90), ("C", 150))]
    a, b = _char(160, 85), _char(340, 155)
    assert assign_zones([a, b], labels) == 2
    assert (a.source.zone, b.source.zone) == ("B3", "C1")


def test_no_zone_without_both_label_sets():
    c = _char(10, 10)
    assert assign_zones([c], [(1, "1", 0, 0), (1, "2", 50, 0)]) == 0 and c.source.zone == ""


def test_fai_workbook_round_trip(tmp_path):
    from pathlib import Path

    from astracad_inspect.pipeline import inspect_drawing
    fix = Path(__file__).parent / "fixtures" / "plate_synthetic.dxf"
    res = inspect_drawing(fix, tmp_path / "run")
    out = write_fai(res.artifacts["plan_json"], tmp_path / "fai.xlsx")
    assert all(ok for _, ok, _ in verify_fai(res.artifacts["plan_json"], out))
    from openpyxl import load_workbook
    wb = load_workbook(out)
    form1 = {r[0]: r[1] for r in wb["Form 1"].iter_rows(min_row=2, values_only=True)}
    assert form1["3. Serial number"] == TO_COMPLETE            # never invented
    plan = json.loads(Path(res.artifacts["plan_json"]).read_text(encoding="utf-8"))
    assert all(r.get("zone") == "" for r in plan["rows"])
    form3 = list(wb["Form 3"].iter_rows(min_row=3, values_only=True))
    assert len(form3) == len(plan["rows"]) and all(r[4] is None for r in form3)   # results blank
    assert all(r[1] == "sheet 1" for r in form3)


def test_dxf_zone_assignment_end_to_end(tmp_path):
    import ezdxf
    from pathlib import Path
    from openpyxl import load_workbook
    from astracad_inspect.export.fai import write_fai, verify_fai
    from astracad_inspect.pipeline import inspect_drawing

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (400, 0), (400, 300), (0, 300), (0, 0)])
    msp.add_text("A", dxfattribs={"insert": (5, 50), "height": 3.5})
    msp.add_text("B", dxfattribs={"insert": (5, 150), "height": 3.5})
    msp.add_text("C", dxfattribs={"insert": (5, 250), "height": 3.5})
    msp.add_text("1", dxfattribs={"insert": (100, 5), "height": 3.5})
    msp.add_text("2", dxfattribs={"insert": (200, 5), "height": 3.5})
    msp.add_text("3", dxfattribs={"insert": (300, 5), "height": 3.5})
    msp.add_linear_dim(base=(200, 160), p1=(180, 150), p2=(220, 150), text="40").render()
    dxf_path = tmp_path / "grid.dxf"
    doc.saveas(dxf_path)

    res = inspect_drawing(dxf_path, tmp_path / "run")
    plan = json.loads(Path(res.artifacts["plan_json"]).read_text(encoding="utf-8"))
    assert len(plan["rows"]) == 1
    assert plan["rows"][0]["zone"] == "B2"
    assert plan["layout"]["balloons"][0]["zone"] == "B2"

    fai_out = write_fai(res.artifacts["plan_json"], tmp_path / "fai.xlsx")
    assert all(ok for _, ok, _ in verify_fai(res.artifacts["plan_json"], fai_out))
    wb_fai = load_workbook(fai_out)
    form3 = list(wb_fai["Form 3"].iter_rows(min_row=3, values_only=True))
    assert form3[0][1] == "sheet 1, zone B2"

    wb_insp = load_workbook(res.artifacts["xlsx"])
    insp_sheet = wb_insp["Inspection table"]
    headers = [c.value for c in insp_sheet[1]]
    assert "Zone" in headers
    zone_idx = headers.index("Zone")
    assert insp_sheet.cell(row=2, column=zone_idx + 1).value == "B2"

    b_sheet = wb_insp["Balloons"]
    b_headers = [c.value for c in b_sheet[1]]
    assert "Zone" in b_headers
    b_zone_idx = b_headers.index("Zone")
    assert b_sheet.cell(row=2, column=b_zone_idx + 1).value == "B2"


def test_dxf_zone_assignment_cabinet_bath_corpus(tmp_path):
    from pathlib import Path
    corpus_dxf = Path(__file__).parent.parent / "_sources" / "corpus_sw_v2" / "cabinet_bath_s1.dxf"
    if not corpus_dxf.exists():
        corpus_dxf = Path(__file__).parent.parent / "_sources" / "corpus_sw" / "cabinet_bath_s1.dxf"
    if not corpus_dxf.exists():
        return
    from astracad_inspect.pipeline import inspect_drawing
    res = inspect_drawing(corpus_dxf, tmp_path / "corpus_run")
    plan = json.loads(Path(res.artifacts["plan_json"]).read_text(encoding="utf-8"))
    zoned_rows = [r for r in plan["rows"] if r.get("zone")]
    assert len(zoned_rows) == len(plan["rows"])
    assert any(r["zone"] == "B6" for r in zoned_rows)
