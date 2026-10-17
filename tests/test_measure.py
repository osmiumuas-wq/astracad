"""Measurement results: entered by a named inspector, judged against the drawing's own limits,
append-only with corrections superseding, and exported/re-opened without inventing anything."""
import json
from pathlib import Path

import pytest

from astracad_inspect.measure import evaluate, read_log, record, verdict, verify_results, write_results
from astracad_web.server import Workspace

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def run(tmp_path):
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"])
    r = w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    plan_path = w.resolve_key(r["key"])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    return w, r["key"], plan_path, plan


def _row(plan, callout):
    return next(r for r in plan["rows"] if r["callout"] == callout)


def test_verdict_uses_inclusive_limits_and_never_judges_without_them():
    row = {"minimum": 99.9, "maximum": 100.1}
    assert verdict(row, 99.9)[0] == "PASS" and verdict(row, 100.1)[0] == "PASS"
    v, why = verdict(row, 100.13)
    assert v == "FAIL" and "0.03" in why and "above the upper" in why
    assert verdict({"minimum": None, "maximum": None}, 25.0)[0] == "NOT_EVALUABLE"
    assert verdict(row, None)[0] == "WITHDRAWN"


def test_record_refuses_what_it_cannot_attribute_or_read(run):
    w, key, plan_path, plan = run
    cid = _row(plan, "100 ±0.1")["cid"]
    for body, why in [({"sample": "SN1", "cid": cid, "value": 100}, "inspector"),
                      ({"inspector": "K M", "cid": cid, "value": 100}, "sample"),
                      ({"inspector": "K M", "sample": "SN1", "cid": "CH-999", "value": 100}, "unknown"),
                      ({"inspector": "K M", "sample": "SN1", "cid": cid, "value": "abc"}, "number"),
                      ({"inspector": "K M", "sample": "SN1", "cid": cid, "value": "nan"}, "finite"),
                      ({"inspector": "K M", "sample": "SN1", "cid": cid, "withdraw": True}, "reason")]:
        with pytest.raises(ValueError, match=why):
            record(plan_path, plan, body)
    assert read_log(plan_path) == []


def test_correction_supersedes_and_history_is_kept(run):
    w, key, plan_path, plan = run
    cid = _row(plan, "100 ±0.1")["cid"]
    record(plan_path, plan, {"inspector": "K M", "sample": "SN1", "cid": cid, "value": "100.2", "instrument": "MIC-07"})
    record(plan_path, plan, {"inspector": "K M", "sample": "SN1", "cid": cid, "value": "100,05",
                             "note": "re-measured after cleaning"})
    ev = evaluate(plan, read_log(plan_path))
    cell = ev["cells"][f"SN1|{cid}"]
    assert cell["value"] == 100.05 and cell["verdict"] == "PASS" and len(cell["history"]) == 1
    assert len(read_log(plan_path)) == 2             # nothing was erased


def test_sample_states_and_capability(run):
    w, key, plan_path, plan = run
    judged = [r for r in plan["rows"] if r.get("minimum") is not None and r.get("status") != "rejected"]
    for s, delta in (("SN1", 0.0), ("SN2", 0.01), ("SN3", -0.01)):
        for r in judged:
            mid = (r["minimum"] + r["maximum"]) / 2
            record(plan_path, plan, {"inspector": "Q I", "sample": s, "cid": r["cid"], "value": mid + delta})
    ev = evaluate(plan, read_log(plan_path))
    st = ev["sample_state"]["SN1"]
    # every judged row passed, but rows without limits cannot be judged: the sample is not complete
    unjudgeable = [r["cid"] for r in plan["rows"] if r.get("minimum") is None and r.get("status") != "rejected"]
    assert st["fails"] == [] and st["state"] == "INCOMPLETE" and st["missing"] == unjudgeable
    cid = judged[0]["cid"]
    c = ev["capability"][cid]
    assert c["n"] == 3 and c["cp"] > 0 and "estimate from 3" in c["note"]
    # one value out of tolerance makes the sample FAIL, whatever else is missing
    r0 = judged[0]
    record(plan_path, plan, {"inspector": "Q I", "sample": "SN2", "cid": r0["cid"], "value": r0["maximum"] + 1})
    assert evaluate(plan, read_log(plan_path))["sample_state"]["SN2"]["state"] == "FAIL"


def test_a_complete_passing_sample_is_not_acceptable_against_an_open_plan(run):
    w, key, plan_path, plan = run
    for r in plan["rows"]:
        lo, hi = r.get("minimum"), r.get("maximum")
        record(plan_path, plan, {"inspector": "Q I", "sample": "SN9", "cid": r["cid"],
                                 "value": (lo + hi) / 2 if lo is not None else 10.0})
    st = evaluate(plan, read_log(plan_path))["sample_state"]["SN9"]
    assert plan["approvable"] is False
    assert st["state"] in ("INCOMPLETE", "NOT_ACCEPTABLE_PLAN_OPEN") and st["state"] != "ACCEPTABLE"


def test_results_and_fai_export_reopen_and_carry_only_recorded_values(run, tmp_path):
    from openpyxl import load_workbook
    w, key, plan_path, plan = run
    r1 = _row(plan, "100 ±0.1")
    w.measure(key, {"inspector": "K M", "sample": "SN-001", "cid": r1["cid"], "value": 100.02, "instrument": "CMM-1"})
    out = w.export_results(key, "SN-001")
    assert all(c["ok"] for c in out["checks"]), out["checks"]
    ws = load_workbook(w.resolve_key(out["fai"]), read_only=True)["Form 3"]
    rows = [r for r in ws.iter_rows(min_row=3, values_only=True) if r and r[0] is not None]
    got = {r[7]: r[4] for r in rows}
    assert got[r1["cid"]].startswith("100.02 mm PASS (K M, CMM-1,")
    assert all(not v for cid, v in got.items() if cid != r1["cid"])    # nothing else invented
    f1 = {r[0]: r[1] for r in load_workbook(w.resolve_key(out["fai"]), read_only=True)["Form 1"].iter_rows(values_only=True)}
    assert f1["3. Serial number"] == "SN-001"
    with pytest.raises(ValueError, match="no measurements recorded for sample"):
        w.export_results(key, "SN-404")


def test_inspection_payload_carries_the_evaluation(run):
    w, key, plan_path, plan = run
    r1 = _row(plan, "100 ±0.1")
    w.measure(key, {"inspector": "K M", "sample": "A1", "cid": r1["cid"], "value": 99.5})
    d = w.inspection(key)
    assert d["measurements"]["cells"][f"A1|{r1['cid']}"]["verdict"] == "FAIL"
    assert d["measurements"]["sample_state"]["A1"]["state"] == "FAIL"
