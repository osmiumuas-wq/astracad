"""Results import: preview writes nothing, every row is checked like a typed value, commit is
all-or-nothing unless partial import is chosen, and imported values trace to the file and row."""
import http.client
import io
import json
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

from astracad_inspect.import_results import resolve_balloon
from astracad_inspect.measure import read_log
from astracad_web.server import Workspace, serve

FIX = Path(__file__).parent / "fixtures"
FUTURE = (date.today() + timedelta(days=100)).isoformat()


@pytest.fixture()
def run(tmp_path):
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"])
    r = w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    plan_path = w.resolve_key(r["key"])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    w.save_instrument({"by": "Q Lab", "id": "CMM-01", "kind": "CMM", "unit": "mm", "range_min": 0,
                       "range_max": 1000, "resolution": 0.0001, "calibration_due": FUTURE, "calibration_ref": "CMM-2026"})
    b = {r["callout"]: r["balloon_no"] for r in plan["rows"]}
    return w, r["key"], plan_path, plan, b


def test_balloon_references():
    plan = {"rows": [{"cid": "CH-001", "balloon_no": 3}, {"cid": "CH-002", "balloon_no": 12}]}
    for ref, cid in (("3", "CH-001"), ("B12", "CH-002"), ("#12", "CH-002"), ("Balloon 3", "CH-001"),
                     ("ch-002", "CH-002"), ("3.0", "CH-001"), ("BAL-12", "CH-002")):
        assert resolve_balloon(ref, plan)["cid"] == cid, ref
    assert resolve_balloon("CIRCLE_7", plan) is None                 # CMM feature names are not guessed
    assert resolve_balloon("CIRCLE_7", plan, {"CIRCLE_7": 12})["cid"] == "CH-002"


def test_long_csv_preview_writes_nothing_then_commit_traces_rows(run):
    w, key, plan_path, plan, b = run
    csv = (f"Serial,Balloon,Actual,Gauge\nSN-1,{b['100 ±0.1']},100.0123,CMM-01\n"
           f"SN-1,{b['50 +0.2/-0']},50.31,CMM-01\nSN-2,{b['100 ±0.1']},99.98,CMM-01\n").encode()
    pv = w.import_results(key, "cmm.csv", csv, {"inspector": "K M"})
    assert pv["layout"] == "long" and pv["summary"] == {"accepted": 3, "refused": 0, "fail": 1}
    assert read_log(plan_path) == []                                 # a preview writes nothing
    out = w.import_results(key, "cmm.csv", csv, {"inspector": "K M", "mode": "commit"})
    log = read_log(plan_path)
    assert len(log) == 3 and all(e["source"]["sha256"] == pv["sha256"] for e in log)
    assert log[0]["source"]["where"] == "row 2" and log[0]["instrument_record"]["calibration_ref"] == "CMM-2026"
    kept = plan_path.parent / out["committed"]["kept_as"]
    assert kept.read_bytes() == csv
    assert w.inspection(key)["measurements"]["sample_state"]["SN-1"]["fails"]


def test_refused_rows_block_commit_unless_partial_import_is_chosen(run):
    w, key, plan_path, plan, b = run
    csv = (f"sample,balloon,value,instrument\nSN-1,{b['100 ±0.1']},100.01,CMM-01\nSN-1,99,1.0,CMM-01\n"
           f"SN-1,{b['25']},abc,CMM-01\nSN-1,{b['100 ±0.1']},100.02,CMM-01\nSN-1,{b['45°']},45.00001,NOPE\n").encode()
    pv = w.import_results(key, "x.csv", csv, {"inspector": "K M"})
    reasons = " | ".join(r["reason"] for r in pv["refused"])
    assert pv["summary"]["accepted"] == 1
    assert "no balloon" in reasons and "not a number" in reasons and "appears again" in reasons
    assert "not in the instrument register" in reasons
    with pytest.raises(ValueError, match="refused"):
        w.import_results(key, "x.csv", csv, {"inspector": "K M", "mode": "commit"})
    assert read_log(plan_path) == []
    w.import_results(key, "x.csv", csv, {"inspector": "K M", "mode": "commit", "skip_rejected": "1"})
    assert len(read_log(plan_path)) == 1


def test_wide_xlsx_and_inch_values_are_converted_with_the_original_kept(run):
    from openpyxl import Workbook
    w, key, plan_path, plan, b = run
    wb = Workbook()
    ws = wb.active
    ws.append(["Balloon", "SN-7", "SN-8"])
    ws.append([b["100 ±0.1"], 3.9375, 3.9370])                       # inches
    buf = io.BytesIO()
    wb.save(buf)
    pv = w.import_results(key, "gauge.xlsx", buf.getvalue(), {"inspector": "K M", "unit": "in", "instrument": "CMM-01"})
    assert pv["layout"] == "wide" and pv["summary"]["accepted"] == 2
    e = pv["accepted"][0]["entry"]
    assert e["value"] == pytest.approx(3.9375 * 25.4) and e["converted_from"] == {"value": 3.9375, "unit": "in"}


def test_needs_a_named_importer_and_a_known_layout(run):
    w, key, plan_path, plan, b = run
    with pytest.raises(ValueError, match="named"):
        w.import_results(key, "x.csv", b"sample,balloon,value\nS,1,1\n", {})
    with pytest.raises(ValueError, match="layout"):
        w.import_results(key, "x.csv", b"a,,c\n1,2,3\n", {"inspector": "K M"})
    with pytest.raises(ValueError, match="csv"):
        w.import_results(key, "x.exe", b"MZ", {"inspector": "K M"})


def test_http_import_preview_and_oversize(run):
    w, key, plan_path, plan, b = run
    httpd = serve(w, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    def post(path, body):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        c.request("POST", path, body=body, headers={"Host": "127.0.0.1", "X-AstraCAD": "1"})
        r = c.getresponse()
        return r.status, json.loads(r.read())
    from urllib.parse import quote
    st, d = post(f"/api/import_results?key={quote(key)}&name=r.csv&inspector=K%20M",
                 f"balloon,value,sample\n{b['100 ±0.1']},100.0,S1\n".encode())
    assert st == 200 and d["summary"]["accepted"] == 1
    st, d = post(f"/api/import_results?key={quote(key)}&name=r.csv&inspector=K%20M", b"x" * (10 * 1024 * 1024 + 1))
    assert st == 413
    httpd.shutdown()
