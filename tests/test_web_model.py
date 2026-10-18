"""Workspace + Gemini: asking is explicit, the build runs exactly the plan that was shown (never a new
answer), a changed stored plan is refused, and Gemini's own choices need the user's acceptance."""
import json

import pytest

from astracad_core import model_gateway
from astracad_web.server import Workspace

from test_model_planner import L_SPEC, L_VALUES, REQ, Fake
from test_web_text_to_cad import Orch


def ws(tmp_path, calls):
    return Workspace(tmp_path / "t.db", [tmp_path / "runs"], orchestrator_factory=lambda db: Orch(calls))


def test_nothing_is_sent_without_a_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(model_gateway, "provider", lambda: None)
    with pytest.raises(PermissionError, match="no model is set up"):
        ws(tmp_path, []).model_plan({"request": REQ})


def test_the_build_runs_exactly_the_plan_shown(tmp_path, monkeypatch):
    fake = Fake({"status": "ok", "spec": L_SPEC, "values": L_VALUES, "assumptions": []})
    monkeypatch.setattr(model_gateway, "provider", lambda: fake)
    calls = []
    w = ws(tmp_path, calls)
    d = w.model_plan({"request": REQ})
    assert d["executable"] and d["provider"] == "fake" and len(fake.prompts) == 1
    assert (tmp_path / "runs" / "model_plans" / f"{d['plan_id']}.json").is_file()
    with pytest.raises(PermissionError):
        w.model_build({"plan_id": d["plan_id"], "authorize": " "})
    r = w.model_build({"plan_id": d["plan_id"], "authorize": "K M"})
    assert len(fake.prompts) == 1                                   # not asked again
    (req, auth), = calls
    assert req.params["spec"]["features"] == L_SPEC["features"] and req.params["spec"]["notes"]["plan_id"] == d["plan_id"]
    assert r["status"] == "SUCCEEDED" and auth.granted_by == "K M"


def test_a_changed_stored_plan_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(model_gateway, "provider", lambda: Fake({"status": "ok", "spec": L_SPEC, "values": L_VALUES}))
    calls = []
    w = ws(tmp_path, calls)
    d = w.model_plan({"request": REQ})
    p = tmp_path / "runs" / "model_plans" / f"{d['plan_id']}.json"
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["exchange"]["response"] = rec["exchange"]["response"].replace('"depth": 8', '"depth": 80')
    p.write_text(json.dumps(rec), encoding="utf-8")
    with pytest.raises(PermissionError, match="changed after it was made"):
        w.model_build({"plan_id": d["plan_id"], "authorize": "K M"})
    with pytest.raises(PermissionError, match="unknown plan"):
        w.model_build({"plan_id": "../../etc", "authorize": "K M"})
    assert calls == []


def test_proposed_values_need_the_users_acceptance(tmp_path, monkeypatch):
    vals = L_VALUES[:3] + [{"name": "thickness", "value": 8, "unit": "mm", "source": "assumed", "quote": ""}]
    monkeypatch.setattr(model_gateway, "provider", lambda: Fake({"status": "ok", "spec": L_SPEC, "values": vals}))
    calls = []
    w = ws(tmp_path, calls)
    d = w.model_plan({"request": REQ})
    assert not d["executable"] and any(a.startswith("Gemini proposed 1 value") for a in d["ambiguities"])
    with pytest.raises(ValueError, match="accept them to build"):
        w.model_build({"plan_id": d["plan_id"], "authorize": "K M"})
    w.model_build({"plan_id": d["plan_id"], "authorize": "K M", "accept": True})
    assert len(calls) == 1
