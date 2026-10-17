"""NCR life cycle and the inspection calculations."""
import json
import math
from pathlib import Path

import pytest

from astracad_mfg import machining as M
from astracad_web.server import Workspace

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def run(tmp_path):
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"])
    r = w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    plan = json.loads(w.resolve_key(r["key"]).read_text(encoding="utf-8"))
    cid = next(x["cid"] for x in plan["rows"] if x["callout"] == "100 ±0.1")
    return w, r["key"], cid


def test_ncr_from_a_failed_value_through_closure(run):
    w, key, cid = run
    w.measure(key, {"inspector": "K M", "sample": "SN-1", "cid": cid, "value": 100.15})
    n = w.ncr_action({"action": "create", "key": key, "cid": cid, "sample": "SN-1", "by": "K M",
                      "description": "length 100.15 over the 100.1 upper limit"})
    assert n["number"].startswith("NCR-") and n["status"] == "OPEN"
    assert n["link"]["actual"] == 100.15 and n["link"]["verdict"] == "FAIL" and n["link"]["maximum"] == 100.1
    n = w.ncr_action({"action": "contain", "number": n["number"], "by": "K M", "text": "batch quarantined in cage 3"})
    assert n["status"] == "CONTAINED"
    with pytest.raises(ValueError, match="second"):
        w.ncr_action({"action": "disposition", "number": n["number"], "by": "K M", "disposition": "USE_AS_IS",
                      "justification": "fit is not affected", "approved_by": "K M"})
    with pytest.raises(ValueError, match="closed after"):
        w.ncr_action({"action": "close", "number": n["number"], "by": "K M", "root_cause": "x",
                      "corrective_action": "y", "verification": "z"})
    n = w.ncr_action({"action": "disposition", "number": n["number"], "by": "K M", "disposition": "USE_AS_IS",
                      "justification": "assembly clearance unaffected (calc 17)", "approved_by": "R Engineer"})
    assert n["disposition"]["approved_by"] == "R Engineer"
    with pytest.raises(ValueError, match="already has a disposition"):
        w.ncr_action({"action": "disposition", "number": n["number"], "by": "K M", "disposition": "SCRAP", "justification": "x"})
    n = w.ncr_action({"action": "close", "number": n["number"], "by": "Q Lead", "root_cause": "tool wear",
                      "corrective_action": "tool life cut to 40 parts", "verification": "next 3 lots in tolerance"})
    assert n["status"] == "CLOSED" and [h["action"] for h in n["history"]] == ["opened", "contain", "disposition", "close"]
    with pytest.raises(ValueError, match="cannot change"):
        w.ncr_action({"action": "cancel", "number": n["number"], "by": "K M", "text": "x"})
    # the FAI for that serial carries the NCR number in Form 3 column 11
    from openpyxl import load_workbook
    out = w.export_results(key, "SN-1")
    rows = list(load_workbook(w.resolve_key(out["fai"]), read_only=True)["Form 3"].iter_rows(min_row=3, values_only=True))
    assert next(r for r in rows if r[7] == cid)[6] == n["number"]
    assert w.inspection(key)["ncrs"][0]["number"] == n["number"]


def test_ncr_numbers_are_sequential_and_inputs_checked(run):
    w, key, cid = run
    a = w.ncr_action({"action": "create", "key": key, "cid": cid, "by": "K M", "description": "a"})
    b = w.ncr_action({"action": "create", "key": key, "checklist": "drawing_review", "item": "notes", "by": "K M", "description": "b"})
    assert int(b["number"][-4:]) == int(a["number"][-4:]) + 1 and b["link"]["checklist"] == "drawing_review"
    with pytest.raises(ValueError, match="description"):
        w.ncr_action({"action": "create", "key": key, "cid": cid, "by": "K M"})
    with pytest.raises(PermissionError):
        w.ncr.get("../../x")
    w.ncr_action({"action": "cancel", "number": a["number"], "by": "K M", "text": "raised in error"})
    assert w.ncr.get(a["number"])["status"] == "CANCELLED"


def test_true_position_rfs_mmc_lmc():
    rfs = M.run("true_position", {"x_actual": 10.03, "y_actual": 19.98, "x_basic": 10, "y_basic": 20, "tolerance_mm": 0.05})
    assert rfs.out("position") == pytest.approx(2 * math.hypot(0.03, 0.02)) and rfs.outputs["conforms"][1] == "FAIL"
    mmc = M.run("true_position", {"x_actual": 10.03, "y_actual": 19.98, "x_basic": 10, "y_basic": 20, "tolerance_mm": 0.05,
                                  "modifier": "MMC", "feature": "hole", "size_actual": 10.04, "size_min": 10.0, "size_max": 10.06})
    assert mmc.out("bonus") == pytest.approx(0.04) and mmc.outputs["conforms"][1] == "PASS"
    pin = M.run("true_position", {"x_actual": 0, "y_actual": 0.01, "x_basic": 0, "y_basic": 0, "tolerance_mm": 0.0,
                                  "modifier": "MMC", "feature": "pin", "size_actual": 9.97, "size_min": 9.95, "size_max": 10.0})
    assert pin.out("bonus") == pytest.approx(0.03) and pin.outputs["conforms"][1] == "PASS"
    bad = M.run("true_position", {"x_actual": 0, "y_actual": 0, "x_basic": 0, "y_basic": 0, "tolerance_mm": 0.1,
                                  "modifier": "LMC", "feature": "hole", "size_actual": 10.2, "size_min": 10.0, "size_max": 10.1})
    assert bad.outputs["conforms"][1] == "FAIL" and any("nonconforming" in f for f in bad.findings)


def test_three_wire_sine_bar_bolt_circle_cycle_time_convert():
    p = 1.5
    w = 0.57735 * p
    e = 9.026                                                     # M10x1.5 basic pitch diameter
    m = e + 3 * w - 0.866025 * p
    assert M.run("three_wire", {"measurement_mm": m, "wire_mm": w, "pitch_mm": p}).out("E_pitch_diameter") == pytest.approx(e)
    assert any("WARNING" in f for f in M.run("three_wire", {"measurement_mm": 10, "wire_mm": 0.5, "pitch_mm": 1.5}).findings)
    h = M.run("sine_bar", {"length_mm": 100, "angle_deg": 30}).out("H")
    assert h == pytest.approx(50.0)
    assert M.run("sine_bar", {"length_mm": 100, "height_mm": h}).out("theta") == pytest.approx(30.0)
    bc = M.run("bolt_circle", {"pcd_mm": 100, "holes": 4, "start_angle_deg": 45})
    assert bc.out("hole_1_x") == pytest.approx(50 * math.cos(math.radians(45))) and bc.out("chord") == pytest.approx(100 * math.sin(math.pi / 4))
    t = M.run("cycle_time", {"process": "turning", "length_mm": 98, "approach_mm": 2, "feed_mm_rev": 0.2, "n_rpm": 1000, "passes": 2})
    assert t.out("t") == pytest.approx(100 / 200 * 2)
    assert M.run("convert", {"value": 1, "from_unit": "in", "to_unit": "mm"}).out("value") == pytest.approx(25.4)
    assert M.run("convert", {"value": 100, "from_unit": "m/min", "to_unit": "sfm"}).out("value") == pytest.approx(328.0839895)
    with pytest.raises(M.CalcError, match="cannot be converted"):
        M.run("convert", {"value": 1, "from_unit": "mm", "to_unit": "psi"})
