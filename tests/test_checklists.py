"""Evidence-linked checklists: auto items read the run's records, every decision is a person's,
an auto item cannot be passed against failing evidence, and sign-off needs a complete list."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from astracad_web.server import Workspace

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def ws_run(tmp_path):
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"])
    r = w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    return w, r["key"]


def test_drawing_review_auto_items_read_the_run(ws_run):
    w, key = ws_run
    st = w.checklist(key, "drawing_review", None)
    auto = {i["id"]: i["auto"] for i in st["items"] if i["auto"]}
    assert auto["units"]["state"] == "PASS"
    assert auto["exports"]["state"] == "PASS"                        # 19/19 read-back checks
    assert auto["no_blocking"]["state"] == "FAIL" and "balloons" in auto["no_blocking"]["evidence"]
    assert auto["interpreted"]["state"] == "FAIL"                    # "86.98 87.05" is escalated
    assert auto["ballooning"]["state"] == "PASS"
    assert st["decided"] == 0 and not st["complete"]


def test_decisions_are_named_reasoned_and_cannot_contradict_evidence(ws_run):
    w, key = ws_run
    base = {"key": key, "template": "drawing_review", "by": "Q Lead"}
    with pytest.raises(ValueError, match="cannot pass"):
        w.checklist_decide({**base, "item": "no_blocking", "result": "PASS"})
    with pytest.raises(ValueError, match="needs a note"):
        w.checklist_decide({**base, "item": "no_blocking", "result": "FAIL"})
    with pytest.raises(ValueError, match="your name"):
        w.checklist_decide({**base, "by": "", "item": "units", "result": "PASS"})
    st = w.checklist_decide({**base, "item": "units", "result": "PASS"})
    d = next(i for i in st["items"] if i["id"] == "units")["decision"]
    assert d["by"] == "Q Lead" and d["evidence_seen"]["state"] == "PASS"


def test_sign_off_needs_every_item_decided_and_none_failed(ws_run):
    w, key = ws_run
    base = {"key": key, "template": "drawing_review", "by": "Q Lead"}
    with pytest.raises(ValueError, match="cannot sign"):
        w.checklist_decide({**base, "item": "__sign__"})
    st = w.checklist(key, "drawing_review", None)
    for i in st["items"]:
        ok = i["auto"] is None or i["auto"]["state"] != "FAIL"
        w.checklist_decide({**base, "item": i["id"], "result": "PASS" if ok else "NA",
                            "note": "" if ok else "accepted by engineering, ECR 12"})
    signed = w.checklist_decide({**base, "item": "__sign__", "note": "released for planning"})
    assert signed["complete"] and signed["signed"]["by"] == "Q Lead"
    # a later decision voids the signature until it is signed again
    after = w.checklist_decide({**base, "item": "notes", "result": "FAIL", "note": "finish callout unclear"})
    assert after["signed"] is None and after["signature_superseded"] and not after["complete"]


def test_fai_readiness_is_per_serial_and_uses_measurements_and_instruments(ws_run, tmp_path):
    w, key = ws_run
    assert w.checklist(key, "fai_readiness", None)["needs_sample"]
    plan = json.loads(w.resolve_key(key).read_text(encoding="utf-8"))
    cid = next(r["cid"] for r in plan["rows"] if r["callout"] == "100 ±0.1")
    w.save_instrument({"by": "Q Lab", "id": "MIC-07", "kind": "micrometer", "unit": "mm", "range_min": 75,
                       "range_max": 125, "resolution": 0.001, "calibration_ref": "C1",
                       "calibration_due": (date.today() + timedelta(days=100)).isoformat()})
    w.measure(key, {"inspector": "K M", "sample": "SN-1", "cid": cid, "value": 100.02, "instrument": "MIC-07"})
    st = w.checklist(key, "fai_readiness", "SN-1")
    auto = {i["id"]: i["auto"] for i in st["items"] if i["auto"]}
    assert auto["instruments"]["state"] == "PASS"
    assert auto["passes"]["state"] == "PASS"
    assert auto["measured"]["state"] == "FAIL" and "missing" in auto["measured"]["evidence"]
    assert auto["plan_releasable"]["state"] == "FAIL"
    out = w.export_checklist(key, "fai_readiness", "SN-1")
    assert all(c["ok"] for c in out["checks"])


def test_unknown_template_and_item_are_refused(ws_run):
    w, key = ws_run
    with pytest.raises(ValueError, match="unknown checklist"):
        w.checklist(key, "nope", None)
    with pytest.raises(ValueError, match="unknown item"):
        w.checklist_decide({"key": key, "template": "drawing_review", "by": "Q", "item": "zzz", "result": "PASS"})
