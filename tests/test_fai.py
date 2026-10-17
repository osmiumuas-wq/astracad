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
    form3 = list(wb["Form 3"].iter_rows(min_row=3, values_only=True))
    assert len(form3) == len(plan["rows"]) and all(r[4] is None for r in form3)   # results blank
