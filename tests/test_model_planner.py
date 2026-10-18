"""Gemini as a planner: its plan is checked, never trusted. Only known operations run; a number counts as
stated only when its quote is really in the request; everything else waits for the user's acceptance."""
import json
import sys
import textwrap
from pathlib import Path

import pytest

from astracad_core import model_gateway
from astracad_core.model_gateway import GeminiAPI, GeminiCLI, ModelError, Reply
from astracad_core.model_planner import accept, interpret_reply, plan, validate_spec

REQ = "SolidWorks L-shaped bracket, 80 x 60 mm outline with 10 mm legs, 8 mm thick, export STEP"
L_SPEC = {"name": "l_bracket", "units": "mm", "features": [
    {"$op": "sketch.loops", "id": "s1", "sketched_on": "front", "args": {"loops": [[
        ["L", 0, 0, 80, 0], ["L", 80, 0, 80, 10], ["L", 80, 10, 10, 10], ["L", 10, 10, 10, 60],
        ["L", 10, 60, 0, 60], ["L", 0, 60, 0, 0]]]}},
    {"$op": "feature.extrude", "id": "f1", "args": {"depth": 8}}]}
L_VALUES = [{"name": "outline width", "value": 80, "unit": "mm", "source": "request", "quote": "80 x 60 mm"},
            {"name": "outline height", "value": 60, "unit": "mm", "source": "request", "quote": "80 x 60 mm"},
            {"name": "leg width", "value": 10, "unit": "mm", "source": "request", "quote": "10 mm legs"},
            {"name": "thickness", "value": 8, "unit": "mm", "source": "request", "quote": "8 mm thick"}]


class Fake:
    name, model = "fake", "test"

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete(self, prompt):
        self.prompts.append(prompt)
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return Reply(text, self.name, self.model)


def reply(**kw):
    return Reply(json.dumps({"status": "ok", "spec": L_SPEC, "values": L_VALUES, "assumptions": [], **kw}), "fake", "test")


def test_a_plan_whose_every_number_is_quoted_from_the_request_is_ready(tmp_path):
    prov = Fake({"status": "ok", "spec": L_SPEC, "values": L_VALUES, "assumptions": []})
    it, rec = plan(REQ, prov, store=tmp_path)
    assert it.executable, (it.missing, it.ambiguities)
    assert [f.provenance for f in it.facts if f.name in ("outline width", "thickness")] == ["USER_SPECIFIED"] * 2
    assert it.spec["features"] == L_SPEC["features"] and it.spec["notes"]["plan_id"] == rec["plan_id"]
    saved = json.loads((tmp_path / f"{rec['plan_id']}.json").read_text(encoding="utf-8"))
    assert saved["exchange"]["request"] == REQ and "Request: " + REQ in saved["exchange"]["prompt"]
    assert REQ in prov.prompts[0]


def test_a_quote_that_is_not_in_the_request_is_only_a_proposal():
    vals = L_VALUES[:3] + [{"name": "thickness", "value": 8, "unit": "mm", "source": "request", "quote": "8 mm plate"}]
    it = interpret_reply(REQ, reply(values=vals))
    f = next(f for f in it.facts if f.name == "thickness")
    assert f.provenance == "AI_PROPOSED" and "not in the request" in f.source_text
    assert not it.executable and "accept them to build" in it.ambiguities[0]
    assert accept(it, "K M").executable and it.fact("proposals_accepted") == "K M"


def test_assumed_and_derived_values_wait_for_acceptance():
    vals = L_VALUES + [{"name": "hole x", "value": 40, "unit": "mm", "source": "derived", "quote": "80 / 2"}]
    spec = {**L_SPEC, "features": L_SPEC["features"] + [
        {"$op": "feature.hole", "id": "h1", "sketched_on": "front", "args": {"diameter": 6, "depth": 8, "center": [40, 5]}}]}
    vals += [{"name": "hole diameter", "value": 6, "unit": "mm", "source": "assumed", "quote": ""},
             {"name": "hole y", "value": 5, "unit": "mm", "source": "assumed", "quote": ""}]
    it = interpret_reply(REQ, reply(spec=spec, values=vals))
    assert not it.executable and "3 value(s)" in it.ambiguities[0]
    assert accept(it, "K M").executable


@pytest.mark.parametrize("spec, why", [
    ({**L_SPEC, "features": [{"$op": "feature.thread", "id": "t", "args": {}}]}, "unknown operation"),
    ({**L_SPEC, "features": [L_SPEC["features"][0], {"$op": "feature.extrude", "id": "f1", "args": {"depth": 8, "draft": 3}}]},
     "unknown arguments"),
    ({**L_SPEC, "features": [L_SPEC["features"][0], {"$op": "feature.extrude", "id": "f1", "args": {"depth": -8}}]},
     "positive number"),
    ({**L_SPEC, "features": [{"$op": "feature.extrude", "id": "f1", "args": {"depth": 8}}]}, "no sketch just before it"),
    ({**L_SPEC, "features": L_SPEC["features"] + [{"$op": "feature.pattern.linear", "id": "p", "args":
                                                   {"feature": "nope", "count": 3, "spacing": 10}}]}, "not an earlier feature"),
])
def test_only_known_operations_with_known_arguments_run(spec, why):
    assert any(why in e for e in validate_spec(spec))
    it = interpret_reply(REQ, reply(spec=spec))
    assert not it.executable and it.spec is None and why in it.ambiguities[0]


def test_a_number_the_model_does_not_account_for_stops_the_plan():
    spec = {**L_SPEC, "features": [L_SPEC["features"][0], {"$op": "feature.extrude", "id": "f1", "args": {"depth": 12}}]}
    it = interpret_reply(REQ, reply(spec=spec))
    assert not it.executable and "does not account for: 12" in it.ambiguities[0]


def test_questions_and_non_json_answers():
    it = interpret_reply(REQ, Reply(json.dumps({"status": "need_info", "questions": ["How thick is the bracket?"]}), "f", "t"))
    assert not it.executable and it.missing == ["How thick is the bracket?"]
    bad = interpret_reply(REQ, Reply("Sure! Here is your bracket.", "f", "t"))
    assert not bad.executable and "not a usable JSON plan" in bad.ambiguities[0]
    fenced = interpret_reply(REQ, Reply("```json\n" + json.dumps({"status": "ok", "spec": L_SPEC, "values": L_VALUES}) + "\n```", "f", "t"))
    assert fenced.executable


def test_a_quoted_size_in_inches_is_converted():
    req = "SolidWorks plate 4 x 2 x 0.5 in"
    spec = {"name": "p", "units": "mm", "features": [
        {"$op": "sketch.rectangle", "id": "s", "sketched_on": "front", "args": {"width": 101.6, "height": 50.8}},
        {"$op": "feature.extrude", "id": "f", "args": {"depth": 12.7}}]}
    vals = [{"name": n, "value": v, "unit": "mm", "source": "request", "quote": "4 x 2 x 0.5 in"}
            for n, v in (("w", 101.6), ("h", 50.8), ("t", 12.7))]
    it = interpret_reply(req, Reply(json.dumps({"status": "ok", "spec": spec, "values": vals}), "f", "t"))
    assert it.executable, it.ambiguities


def test_the_request_never_travels_on_the_command_line(tmp_path):
    """A stand-in `gemini` records its argv and stdin: the request must only be on stdin, and the CLI must be
    asked to stay read-only when it supports that."""
    fake = tmp_path / "fake_gemini.py"
    fake.write_text(textwrap.dedent('''
        import json, sys
        if "--help" in sys.argv:
            print("--approval-mode  default | auto_edit | yolo | plan"); sys.exit(0)
        data = sys.stdin.read()
        open(sys.argv[0] + ".log", "w", encoding="utf-8").write(json.dumps({"argv": sys.argv[1:], "stdin": data}))
        print(json.dumps({"response": json.dumps({"status": "need_info", "questions": ["q"]}), "stats": {}}))
    '''), encoding="utf-8")
    shim = tmp_path / "gemini.cmd"
    shim.write_text(f'@"{sys.executable}" "{fake}" %*\n', encoding="utf-8")
    cli = GeminiCLI(exe=str(shim))
    hostile = 'thread M8 & del /q C:\\ "--yolo" $(rm -rf /)'
    r = cli.complete("INSTRUCTIONS\nRequest: " + hostile)
    log = json.loads((tmp_path / "fake_gemini.py.log").read_text(encoding="utf-8"))
    assert hostile not in " ".join(log["argv"]) and hostile in log["stdin"]
    assert log["argv"][log["argv"].index("--approval-mode") + 1] == "plan" and "--output-format" in log["argv"]
    assert json.loads(r.text)["status"] == "need_info"


def test_the_api_key_is_sent_as_a_header_never_in_the_url(monkeypatch):
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}).encode()

    def fake_urlopen(req, timeout):
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        return Resp()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    monkeypatch.setattr(model_gateway.urllib.request, "urlopen", fake_urlopen)
    GeminiAPI(model="gemini-test").complete("hello")
    assert "test-key-123" not in seen["url"] and seen["headers"].get("X-goog-api-key") == "test-key-123"
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(ModelError, match="GEMINI_API_KEY"):
        GeminiAPI().complete("hello")


def test_switched_off_means_off(monkeypatch):
    monkeypatch.setenv("ASTRACAD_MODEL_PROVIDER", "off")
    assert model_gateway.provider() is None and model_gateway.status()["available"] is False
