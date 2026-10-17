"""Instrument register: attributed changes, calibration status, and the checks a measurement
must pass - or the warning it carries - before it is recorded."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from astracad_inspect.instruments import check, load, status, upsert
from astracad_web.server import Workspace

FIX = Path(__file__).parent / "fixtures"
FUTURE = (date.today() + timedelta(days=200)).isoformat()


def _mic(path, **kw):
    body = {"by": "Q Lab", "id": "MIC-07", "kind": "micrometer", "unit": "mm", "range_min": 75,
            "range_max": 125, "resolution": 0.001, "calibration_due": FUTURE, "calibration_ref": "CAL-2291"}
    body.update(kw)
    return upsert(path, body)


def test_changes_are_attributed_and_kept(tmp_path):
    p = tmp_path / "instruments.json"
    _mic(p)
    _mic(p, resolution=0.002, by="R Lab")
    i = load(p)["instruments"]["MIC-07"]
    assert [h["action"] for h in i["history"]] == ["added", "changed"]
    assert i["history"][1]["by"] == "R Lab" and i["history"][1]["changes"] == {"resolution": [0.001, 0.002]}
    assert _mic(p, resolution=0.002, by="R Lab")["history"] == i["history"]      # no-op is not logged


@pytest.mark.parametrize("bad,why", [({"by": ""}, "your name"), ({"id": "../x"}, "instrument ID"),
                                      ({"range_max": 10}, "above the minimum"), ({"resolution": 0}, "positive"),
                                      ({"calibration_due": "next year"}, "YYYY-MM-DD"), ({"kind": "laser"}, "kind")])
def test_register_refuses_unusable_entries(tmp_path, bad, why):
    with pytest.raises(ValueError, match=why):
        _mic(tmp_path / "i.json", **bad)


def test_calibration_status_by_date():
    inst = {"calibration_due": "2026-11-01", "status": "active"}
    assert status(inst, date(2026, 10, 17))["state"] == "DUE_SOON"
    assert status(inst, date(2026, 6, 1))["state"] == "CALIBRATED"
    assert status(inst, date(2026, 11, 2))["state"] == "EXPIRED"
    assert status({**inst, "status": "retired"}, date(2026, 6, 1))["state"] == "RETIRED"


def test_measurement_checks(tmp_path):
    p = tmp_path / "i.json"
    _mic(p)
    reg = load(p)
    row = {"unit": "mm", "minimum": 99.9, "maximum": 100.1}
    snap, warn = check(reg, "MIC-07", 100.004, row)
    assert snap["calibration_ref"] == "CAL-2291" and warn == []
    for value, why in ((100.0004, "finer than"), (130.0, "outside")):
        with pytest.raises(ValueError, match=why):
            check(reg, "MIC-07", value, row)
    with pytest.raises(ValueError, match="not in the instrument register"):
        check(reg, "CAL-99", 100.0, row)
    # inch characteristic measured on a metric instrument: converted for range and resolution
    assert check(reg, "MIC-07", 3.937, {"unit": "in"})[0]["id"] == "MIC-07"
    # the 10:1 rule is a warning, not a refusal
    _mic(p, id="CAL-01", kind="caliper", range_min=0, range_max=150, resolution=0.01)
    snap, warn = check(load(p), "CAL-01", 100.02, {"unit": "mm", "minimum": 99.98, "maximum": 100.02})
    assert snap and any("coarser than a tenth" in w for w in warn)
    # exactly 10:1 (0.01 against a 0.1 band) meets the rule: no warning
    assert check(load(p), "CAL-01", 100.02, {"unit": "mm", "minimum": 99.95, "maximum": 100.05})[1] == []


def test_expired_and_retired_instruments_are_refused(tmp_path):
    p = tmp_path / "i.json"
    _mic(p, calibration_due=(date.today() - timedelta(days=1)).isoformat())
    with pytest.raises(ValueError, match="calibration expired"):
        check(load(p), "MIC-07", 100.0, {"unit": "mm"})
    _mic(p, status="retired")
    with pytest.raises(ValueError, match="retired"):
        check(load(p), "MIC-07", 100.0, {"unit": "mm"})


def test_workspace_records_instrument_snapshot_and_refuses_expired(tmp_path):
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"])
    r = w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    plan = json.loads(w.resolve_key(r["key"]).read_text(encoding="utf-8"))
    cid = next(x["cid"] for x in plan["rows"] if x["callout"] == "100 ±0.1")
    w.save_instrument({"by": "Q Lab", "id": "MIC-07", "kind": "micrometer", "unit": "mm", "range_min": 75,
                       "range_max": 125, "resolution": 0.001, "calibration_due": FUTURE, "calibration_ref": "C-1"})
    out = w.measure(r["key"], {"inspector": "K M", "sample": "SN1", "cid": cid, "value": 100.012, "instrument": "MIC-07"})
    assert out["entry"]["instrument_record"]["calibration_ref"] == "C-1"
    w.save_instrument({"by": "Q Lab", "id": "MIC-07", "kind": "micrometer", "unit": "mm", "range_min": 75,
                       "range_max": 125, "resolution": 0.001, "calibration_ref": "C-1",
                       "calibration_due": (date.today() - timedelta(days=3)).isoformat()})
    with pytest.raises(ValueError, match="calibration expired"):
        w.measure(r["key"], {"inspector": "K M", "sample": "SN2", "cid": cid, "value": 100.0, "instrument": "MIC-07"})
    lst = w.instruments()["instruments"]
    assert lst[0]["calibration"]["state"] == "EXPIRED"


def test_converted_reading_cannot_be_more_precise_than_the_instrument(tmp_path):
    p = tmp_path / "i.json"
    _mic(p)
    with pytest.raises(ValueError, match="finer than"):
        check(load(p), "MIC-07", 3.937012, {"unit": "in"})    # 1e-6 in << 0.001 mm (3.9e-5 in)
