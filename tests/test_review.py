"""Review in the workspace: decisions are recorded with an audit trail, applied only by re-issuing
a NEW run from the unchanged drawing, and validated before anything is written."""
import json
import shutil
from pathlib import Path

import pytest

from astracad_web.server import Workspace

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def ws(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(FIX / "plate_synthetic.dxf", src / "plate.dxf")
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"])
    r = w.run_inspection(str(src / "plate.dxf"), None, None)
    w._key = r["key"]
    w._src = src / "plate.dxf"
    return w


def _plan(w, key):
    return json.loads(w.resolve_key(key).read_text(encoding="utf-8"))


def test_review_is_recorded_and_logged_but_does_not_edit_the_plan(ws):
    before = ws.resolve_key(ws._key).read_bytes()
    st = ws.review(ws._key, {"reviewer": "K M", "action": "approve", "cid": "CH-005"})
    assert st["review"]["characteristics"] == [{"cid": "CH-005", "reviewer": "K M", "approve": True}]
    assert st["log"][0]["action"] == "approve" and st["log"][0]["reviewer"] == "K M"
    assert ws.resolve_key(ws._key).read_bytes() == before


def test_reissue_makes_a_new_run_with_the_decisions_applied(ws):
    ws.review(ws._key, {"reviewer": "KM", "action": "approve", "cid": "CH-005"})
    ws.review(ws._key, {"reviewer": "KM", "action": "correct", "cid": "CH-006",
                        "tolerance": {"kind": "symmetric", "plus_minus": 0.1}})
    ws.review(ws._key, {"reviewer": "KM", "action": "reject", "cid": "CH-008", "reason": "not a feature"})
    ws.review(ws._key, {"reviewer": "KM", "action": "dismiss", "text": "86.98 87.05",
                        "reason": "title note"})
    new = ws.reissue(ws._key)
    assert new["key"] != ws._key and "_rev2" in new["folder"]
    rows = {r["cid"]: r for r in _plan(ws, new["key"])["rows"]}
    assert rows["CH-005"]["status"] == "approved" and rows["CH-005"]["provenance"] == "human_approved"
    assert (rows["CH-006"]["minimum"], rows["CH-006"]["maximum"]) == (24.9, 25.1)
    assert rows["CH-008"]["status"] == "rejected"
    assert not _plan(ws, new["key"])["uninterpreted"]
    assert ws.inspection(ws._key)["plan"]["rows"][4]["status"] != "approved"   # original untouched


def test_reissue_refuses_when_the_drawing_changed(ws):
    ws.review(ws._key, {"reviewer": "KM", "action": "approve", "cid": "CH-001"})
    ws._src.write_bytes(ws._src.read_bytes() + b"\n999\nEOF\n")
    with pytest.raises(ValueError, match="changed"):
        ws.reissue(ws._key)


@pytest.mark.parametrize("body,msg", [
    ({"reviewer": "", "action": "approve", "cid": "CH-001"}, "reviewer"),
    ({"reviewer": "<script>", "action": "approve", "cid": "CH-001"}, "reviewer"),
    ({"reviewer": "KM", "action": "approve", "cid": "CH-999"}, "unknown"),
    ({"reviewer": "KM", "action": "reject", "cid": "CH-001"}, "reason"),
    ({"reviewer": "KM", "action": "correct", "cid": "CH-001", "nominal": "abc"}, "number"),
    ({"reviewer": "KM", "action": "correct", "cid": "CH-001", "nominal": "inf"}, "finite"),
    ({"reviewer": "KM", "action": "correct", "cid": "CH-001", "tolerance": {"kind": "magic"}}, "tolerance"),
    ({"reviewer": "KM", "action": "delete", "cid": "CH-001"}, "unsupported"),
])
def test_invalid_review_input_is_refused_and_nothing_written(ws, body, msg):
    with pytest.raises(ValueError, match=msg):
        ws.review(ws._key, body)
    st = ws.review_state(ws._key)
    assert st["review"] == {} and st["log"] == []


def test_review_cannot_target_a_file_outside_the_runs(ws):
    with pytest.raises(PermissionError):
        ws.review("0/../t.db", {"reviewer": "KM", "action": "approve", "cid": "CH-001"})


def test_reissued_run_names_the_reviewer_on_every_change_and_links_its_parent(ws):
    ws.review(ws._key, {"reviewer": "A Reviewer", "action": "correct", "cid": "CH-006",
                        "tolerance": {"kind": "symmetric", "plus_minus": 0.1}})
    new = ws.reissue(ws._key)
    c = next(c for c in _plan(ws, new["key"])["characteristics"] if c["cid"] == "CH-006")
    assert any("by A Reviewer" in h["actor"] and h["action"] == "tolerance_overridden" for h in c["history"])
    st = ws.review_state(new["key"])
    assert st["parent"] == ws._key and st["applied_log"][0]["reviewer"] == "A Reviewer"
