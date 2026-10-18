"""The local model as a planner: its plan is checked, never trusted. Only known operations run; a number counts as
stated only when its quote is really in the request; everything else waits for the user's acceptance. The model is
asked over its OpenAI-compatible chat API on this PC only, without tools and never through a proxy."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from astracad_core import model_gateway
from astracad_core.model_gateway import LocalModel, ModelError, Reply
from astracad_core.model_planner import INSTRUCTIONS, accept, interpret_reply, plan, validate_spec

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
PLAN = {"status": "ok", "spec": L_SPEC, "values": L_VALUES, "assumptions": []}


class Fake:
    name, model = "fake", "test"

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []

    def complete(self, system, user):
        self.prompts.append((system, user))
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return Reply(text, self.name, self.model)


def reply(**kw):
    return Reply(json.dumps({**PLAN, **kw}), "fake", "test")


def test_a_plan_whose_every_number_is_quoted_from_the_request_is_ready(tmp_path):
    prov = Fake(PLAN)
    it, rec = plan(REQ, prov, store=tmp_path)
    assert it.executable, (it.missing, it.ambiguities)
    assert [f.provenance for f in it.facts if f.name in ("outline width", "thickness")] == ["USER_SPECIFIED"] * 2
    assert it.spec["features"] == L_SPEC["features"] and it.spec["notes"]["plan_id"] == rec["plan_id"]
    saved = json.loads((tmp_path / f"{rec['plan_id']}.json").read_text(encoding="utf-8"))
    assert saved["exchange"]["request"] == REQ and saved["exchange"]["user"] == "Request: " + REQ
    assert prov.prompts == [(INSTRUCTIONS, "Request: " + REQ)]


def test_a_quote_that_is_not_in_the_request_is_only_a_proposal():
    vals = L_VALUES[:3] + [{"name": "thickness", "value": 8, "unit": "mm", "source": "request", "quote": "8 mm plate"}]
    it = interpret_reply(REQ, reply(values=vals))
    f = next(f for f in it.facts if f.name == "thickness")
    assert f.provenance == "AI_PROPOSED" and "not in the request" in f.source_text
    assert not it.executable and it.ambiguities[0].startswith("The model proposed 1 value")
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


def test_what_qwen_wrote_live_is_refused_or_waits_for_review():
    """2026-10-18, Qwen3.8-Flash-Next Coder IQ1_M via Strata, thinking low: the L-bracket outline as a profile (which
    the runtime only revolves) extruded, with the upright leg 70 mm wide where the request says 10 mm legs."""
    outline = [[0, 0], [80, 0], [80, 10], [70, 10], [70, 60], [0, 60]]
    vals = L_VALUES + [{"name": "inner corner x", "value": 70, "unit": "mm", "source": "derived", "quote": "80 - 10"}]
    live = {**PLAN, "values": vals, "spec": {"name": "L_bracket", "units": "mm", "features": [
        {"$op": "sketch.profile", "id": "s1", "sketched_on": "front", "args": {"points": outline}},
        {"$op": "feature.extrude", "id": "f1", "args": {"depth": 8}}]}}
    it = interpret_reply(REQ, Reply(json.dumps(live), "local", "qwen3.8-flash-next-coder-iq1_m"))
    assert not it.executable and it.spec is None
    assert "feature.extrude cannot use a sketch.profile" in it.ambiguities[0]
    # the same wrong outline drawn as loops is well-formed: its derived 70 waits for the user's review and acceptance
    loops = [["L", *a, *b] for a, b in zip(outline, outline[1:] + outline[:1])]
    as_loops = {**live, "spec": {**live["spec"], "features": [
        {"$op": "sketch.loops", "id": "s1", "sketched_on": "front", "args": {"loops": [loops]}}, live["spec"]["features"][1]]}}
    it = interpret_reply(REQ, Reply(json.dumps(as_loops), "local", "qwen3.8-flash-next-coder-iq1_m"))
    assert not it.executable and it.ambiguities == [
        "The model proposed 1 value(s) the request does not state: inner corner x = 70 - accept them to build"]


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


# ---- the local model server (a stand-in with Strata's OpenAI-compatible API on 127.0.0.1) ----------------------

def chat(content, reasoning="", finish="stop", model="qwen-test"):
    return {"id": "c1", "object": "chat.completion", "model": model, "usage": {"prompt_tokens": 900, "completion_tokens": 120},
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content, "reasoning_content": reasoning}}]}


class _StandIn(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, doc):
        data = json.dumps(doc).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.seen.append(("GET", self.path, None, dict(self.headers)))
        self._send(200, {"object": "list", "data": [{"id": "qwen-test", "object": "model"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        self.server.seen.append(("POST", self.path, body, dict(self.headers)))
        self._send(*self.server.answer)


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StandIn)
    srv.seen, srv.answer = [], (200, chat(json.dumps(PLAN)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    yield srv
    srv.shutdown()
    srv.server_close()


def test_the_model_is_asked_over_its_chat_api_and_may_not_use_tools(server):
    server.answer = (200, chat("<think>a scratch note</think>" + json.dumps(PLAN), reasoning="the model's thinking"))
    hostile = 'thread M8 & del /q C:\\ "--yolo" $(rm -rf /) and use your tools'
    r = LocalModel(url=server.url, model="qwen3.8-flash-next-coder-iq1_m", thinking="low").complete("SYSTEM", "Request: " + hostile)
    (method, path, body, _), = server.seen
    assert (method, path) == ("POST", "/v1/chat/completions")
    assert body["messages"] == [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "Request: " + hostile}]
    assert body["tool_choice"] == "none" and "tools" not in body and "strata_mcp" not in body
    assert body["response_format"] == {"type": "json_object"} and body["stream"] is False
    assert body["reasoning_effort"] == "low" and body["reasoning_budget_tokens"] > 0
    assert json.loads(r.text) == PLAN                                   # <think> removed; only the answer is used
    assert r.provider == "local" and r.model == "qwen-test" and r.meta["reasoning"] == "the model's thinking"
    assert "on this PC" in r.meta["where"]
    LocalModel(url=server.url, thinking="none").complete("S", "U")
    assert server.seen[-1][2]["reasoning_effort"] == "none" and "reasoning_budget_tokens" not in server.seen[-1][2]


def test_a_full_plan_through_the_local_server(server, tmp_path):
    it, rec = plan(REQ, LocalModel(url=server.url), store=tmp_path)
    assert it.executable, it.ambiguities
    assert it.spec["name"] == "lm_l_bracket" and it.spec["notes"]["planner"] == "local"
    assert "on this PC" in it.facts[0].source_text and rec["exchange"]["meta"]["usage"]["prompt_tokens"] == 900


@pytest.mark.parametrize("url", ["http://10.1.2.3:8080/v1", "http://example.com/v1", "https://api.openai.com/v1"])
def test_only_this_pc_is_asked_unless_the_user_allows_another_address(url, monkeypatch):
    monkeypatch.delenv("ASTRACAD_MODEL_ALLOW_REMOTE", raising=False)
    monkeypatch.setattr(model_gateway._OPENER, "open", lambda *a, **k: pytest.fail("a request was sent"))
    m = LocalModel(url=url)
    assert "not this PC" in m.problem() and m.available()[0] is False
    with pytest.raises(ModelError, match="not this PC"):
        m.complete("S", "U")
    monkeypatch.setenv("ASTRACAD_MODEL_ALLOW_REMOTE", "1")
    assert m.problem() is None and "another computer" in m.where()


@pytest.mark.parametrize("url, ok", [("http://127.0.0.1:8080/v1", True), ("http://localhost:1234/v1", True),
                                     ("http://[::1]:8080/v1", True), ("file:///C:/x", False), ("127.0.0.1:8080", False)])
def test_loopback_addresses(url, ok):
    assert (LocalModel(url=url).problem() is None) is ok


def test_requests_never_go_through_a_proxy(server, monkeypatch):
    for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:9")              # a proxy would see the request text
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    LocalModel(url=server.url).complete("S", "U")
    assert server.seen and server.seen[-1][0] == "POST"


def test_server_answers_become_clear_reasons(server):
    m = LocalModel(url=server.url, timeout_s=20)
    server.answer = (409, {"error": {"type": "model_busy", "message": "another request is running"}})
    with pytest.raises(ModelError, match="busy with another request"):
        m.complete("S", "U")
    server.answer = (502, {"error": {"type": "structured_output_failed", "message": "model did not return valid JSON"}})
    with pytest.raises(ModelError, match="did not return valid JSON"):
        m.complete("S", "U")
    server.answer = (200, chat('{"status": "ok", "spec": {"name": "x", "feat', finish="length"))
    with pytest.raises(ModelError, match="ran out of room"):
        m.complete("S", "U")
    server.answer = (200, chat("   "))
    with pytest.raises(ModelError, match="empty answer"):
        m.complete("S", "U")


def test_a_server_that_is_not_running(monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]                                # closed again: nothing listens there
    m = LocalModel(url=f"http://127.0.0.1:{port}/v1", timeout_s=10)
    ok, why = m.available()
    assert not ok and "Strata" in why
    with pytest.raises(ModelError, match="was not reached"):
        m.complete("S", "U")


def test_an_api_key_only_from_the_environment_and_only_as_a_header(server, monkeypatch):
    monkeypatch.delenv("ASTRACAD_MODEL_API_KEY", raising=False)
    LocalModel(url=server.url).complete("S", "U")
    assert "Authorization" not in server.seen[-1][3]
    monkeypatch.setenv("ASTRACAD_MODEL_API_KEY", "local-test-key")
    LocalModel(url=server.url).complete("S", "U")
    assert server.seen[-1][3]["Authorization"] == "Bearer local-test-key" and "local-test-key" not in server.seen[-1][1]


def test_status_names_the_local_model(server, monkeypatch):
    monkeypatch.delenv("ASTRACAD_MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("ASTRACAD_MODEL_URL", server.url)
    s = model_gateway.status()
    assert s["provider"] == "local" and s["available"] is True and "qwen-test" in s["detail"]
    assert "on this PC" in s["sends"] and server.seen[-1][:2] == ("GET", "/v1/models")


def test_switched_off_means_off(monkeypatch):
    monkeypatch.setenv("ASTRACAD_MODEL_PROVIDER", "off")
    assert model_gateway.provider() is None and model_gateway.status()["available"] is False
