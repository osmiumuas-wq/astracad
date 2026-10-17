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


CUSTOM = {"by": "Q Lead", "id": "deburr-check", "title": "Deburr and visual check", "per_sample": True,
          "items": [{"id": "edges", "type": "check", "text": "All edges broken 0.2-0.5 mm"},
                    {"id": "flatness", "type": "numeric", "text": "Flatness of face A", "unit": "mm", "max": 0.05},
                    {"id": "hardness", "type": "numeric", "text": "Hardness", "unit": "HRC", "min": 58, "max": 62},
                    {"id": "operator", "type": "text", "text": "Operator stamp"},
                    {"id": "limits_ok", "type": "auto", "auto": "units"}]}


def test_custom_template_numeric_items_are_judged_by_their_limits(ws_run):
    w, key = ws_run
    t = w.save_checklist_template(CUSTOM)
    assert t["version"] == 1 and t["items"][4]["text"].startswith("Units")
    base = {"key": key, "template": "deburr-check", "sample": "SN-1", "by": "K M"}
    st = w.checklist_decide({**base, "item": "flatness", "value": "0.03"})
    assert next(i for i in st["items"] if i["id"] == "flatness")["decision"]["result"] == "PASS"
    st = w.checklist_decide({**base, "item": "hardness", "value": "63.5"})
    d = next(i for i in st["items"] if i["id"] == "hardness")["decision"]
    assert d["result"] == "FAIL" and "above the maximum 62" in d["judged"] and "58 .. 62" in d["judged"] and d["unit"] == "HRC"
    with pytest.raises(ValueError, match="number"):
        w.checklist_decide({**base, "item": "flatness", "value": "flat"})
    with pytest.raises(ValueError, match="answer"):
        w.checklist_decide({**base, "item": "operator"})
    st = w.checklist_decide({**base, "item": "operator", "text": "stamp 17"})
    assert next(i for i in st["items"] if i["id"] == "operator")["decision"]["result"] == "RECORDED"
    assert "hardness" in st["failed"] and not st["complete"]
    out = w.export_checklist(key, "deburr-check", "SN-1")
    assert all(c["ok"] for c in out["checks"])


def test_template_versions_and_pinning(ws_run):
    w, key = ws_run
    w.save_checklist_template(CUSTOM)
    base = {"key": key, "template": "deburr-check", "sample": "SN-2", "by": "K M"}
    w.checklist_decide({**base, "item": "flatness", "value": "0.04"})
    # tighten the limit: a new version; the instance already started stays on version 1
    t2 = w.save_checklist_template({**CUSTOM, "items": [{**CUSTOM["items"][1], "max": 0.02}] + CUSTOM["items"][2:]})
    assert t2["version"] == 2 and len(t2["versions"]) == 2
    st = w.checklist(key, "deburr-check", "SN-2")
    assert st["version"] == 1 and len(st["items"]) == 5
    fresh = w.checklist(key, "deburr-check", "SN-3")
    assert fresh["version"] == 2 and len(fresh["items"]) == 4
    assert w.save_checklist_template({**CUSTOM, "items": t2["items"]})["version"] == 2    # unchanged: no new version


@pytest.mark.parametrize("bad,why", [({"id": "drawing_review"}, "built-in"), ({"title": ""}, "title"),
                                      ({"items": []}, "at least one"),
                                      ({"items": [{"id": "aa", "type": "numeric", "text": "x", "min": 5, "max": 1}]}, "above"),
                                      ({"items": [{"id": "aa", "type": "auto", "auto": "nope"}]}, "automatic"),
                                      ({"items": [{"id": "a", "text": "x"}]}, "invalid"),
                                      ({"items": [{"id": "aa", "text": "x"}, {"id": "aa", "text": "y"}]}, "repeated"),
                                      ({"by": ""}, "name")])
def test_template_validation(ws_run, bad, why):
    w, key = ws_run
    with pytest.raises(ValueError, match=why):
        w.save_checklist_template({**CUSTOM, **bad})
