"""The decision layer is a set of rules, so the tests are its truth table.

Each test states the situation, the ruling that must follow, and - where it matters - the reason
that must appear in the record, because a ruling whose reason cannot be read is not reviewable.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from astracad_decide import engine, model, outcome, report
from astracad_decide.cli import main

SHA = "a" * 64
FIX = Path(__file__).parent / "fixtures"


def char(cid="CH-001", callout="100 +/-0.1", state="parsed", review="detected",
         limits=(99.9, 100.1), checks=(), issues=()):
    return {"cid": cid, "kind": "linear", "raw_callout": callout, "nominal": 100.0,
            "unit": "mm", "tolerance": {}, "count": 1, "state": state, "review": review,
            "source": {"entity_handle": "1F"}, "geometry_value": None, "limits": limits,
            "balloon_no": 1, "checks": list(checks), "issues": list(issues), "history": []}


def chk(name="geometry_cross_check", result="passed", expected="100.000000",
        actual="100.000000", why=None, delta=0.0):
    return {"name": name, "outcome": result, "expected": expected, "actual": actual,
            "why": why, "delta": delta}


def plan(chars=None, checks=(), approvable=True, gate="releasable", unplaced=None,
         uninterpreted=None, revision_source="stated on the drawing", exceptions=(),
         sha=SHA):
    return {"source_document": "C:/work/plate.dxf", "source_sha256": sha,
            "revision": "B", "revision_source": revision_source, "unit": "mm",
            "page_count": 1, "characteristics": list(chars or []), "rows": [],
            "checks": list(checks), "issues": [], "uninterpreted": list(uninterpreted or []),
            "layout": {"unplaced": list(unplaced or []), "balloons": [], "pages": 1},
            "coverage": {}, "title_block": {}, "notes": [], "warnings": [],
            "approvable": approvable, "gate": gate, "accepted_exceptions": list(exceptions)}


def rec(store, plan_obj, actor="K M"):
    return store.append(engine.evaluate(plan_obj, actor=actor)).to_dict()


# ------------------------------------------------------------------ the plan ruling
def test_release_when_every_row_passed_and_the_gate_is_open():
    d = engine.evaluate(plan([char(checks=[chk()])]), actor="K M")
    assert d.disposition == model.RELEASE
    assert d.items[0]["disposition"] == model.ACCEPT
    assert d.confidence.floor == 0.9
    assert d.confidence.method == "weakest_link"
    assert any(a["disposition"] == model.HOLD for a in d.alternatives)


def test_a_warning_from_the_plan_rides_on_the_row_it_belongs_to():
    """A row can be judged and still carry a caveat: a value exactly on its limit is zero margin,
    not a defect. The caveat has to reach the record, or the reader sees only the verdict."""
    zero = {"code": "zero_margin", "severity": "warning",
            "message": "the drawn geometry sits exactly on the printed resolution"}
    d = engine.evaluate(plan([char(issues=[zero])]), actor="K M")
    assert d.items[0]["notes"] == [zero["message"]]
    assert d.disposition == model.RELEASE


def test_a_failed_check_is_a_blocking_contradiction_not_a_warning():
    d = engine.evaluate(plan([char(checks=[chk(result="failed", actual="100.400000")])]),
                        actor="K M")
    assert d.disposition == model.HOLD
    assert d.items[0]["disposition"] == model.HOLD
    assert d.contradictions[0].code == "VALUE_VS_GEOMETRY"
    assert d.contradictions[0].severity == "blocking"
    assert "100.4" in d.contradictions[0].message


def test_a_callout_without_limits_is_not_judgeable_and_is_never_a_pass():
    d = engine.evaluate(plan([char(limits=None), char("CH-002", checks=[chk()])]), actor="K M")
    assert d.items[0]["disposition"] == model.NOT_JUDGEABLE
    assert d.items[1]["disposition"] == model.ACCEPT
    assert any(u.code == "no_limits" and u.blocks == "PASS/FAIL" for u in d.unknowns)
    assert d.disposition == model.RELEASE
    assert "cannot be judged" in d.rationale


def test_a_source_with_nothing_usable_is_rejected():
    d = engine.evaluate(plan([char(state="unresolved", limits=None)]), actor="K M")
    assert d.disposition == model.REJECT
    assert any(a["disposition"] == model.REJECT for a in d.alternatives) is False


def test_a_file_with_no_characteristics_is_not_evaluable():
    d = engine.evaluate(plan([]), actor="K M")
    assert d.disposition == model.NOT_EVALUABLE
    assert any(u.code == "no_characteristics" for u in d.unknowns)


def test_two_sources_that_disagree_stop_the_release():
    sources = {"readings": {"CH-001": {"dxf_text": "100 +/-0.1", "ocr_a": "100 +/-0.2"}}}
    d = engine.evaluate(plan([char(checks=[chk()])]), actor="K M", sources=sources)
    assert d.disposition == model.HOLD
    assert [c.code for c in d.contradictions] == ["SOURCE_DISAGREEMENT"]


def test_two_sources_that_agree_in_spacing_and_case_do_not_disagree():
    sources = {"readings": {"CH-001": {"dxf_text": " 100 +/-0.1 ", "ocr_a": "100+/-0.1"}}}
    d = engine.evaluate(plan([char(checks=[chk()])]), actor="K M", sources=sources)
    assert d.contradictions == []
    assert d.disposition == model.RELEASE


def test_an_accepted_exception_is_kept_as_a_waiver_and_never_as_a_pass():
    d = engine.evaluate(
        plan([char(checks=[chk()])],
             exceptions=[chk(name="drawing_ink_present", result="not_verifiable")]),
        actor="K M")
    waivers = [n for n in d.evidence if n.kind == "human_waiver"]
    assert len(waivers) == 1
    assert waivers[0].stance == "waived"
    assert waivers[0].weight == model.EVIDENCE_WEIGHTS["human_waiver"]
    assert waivers[0].nid not in {n.nid for n in d.evidence if n.stance == "supports"}


def test_the_subject_says_so_when_the_revision_was_only_assumed():
    d = engine.evaluate(plan([char(checks=[chk()])], revision_source="defaulted: none found"),
                        actor="K M")
    assert d.subject["revision_stated"] is False
    assert any(u.code == "revision_not_stated" for u in d.unknowns)
    assert "assumed" in report.render(d.to_dict())


# ------------------------------------------------------------------------ refusals
def test_a_ruling_without_a_named_person_is_refused():
    with pytest.raises(ValueError, match="named actor"):
        engine.evaluate(plan([char()]), actor="   ")


def test_a_plan_without_a_source_hash_is_refused():
    with pytest.raises(ValueError, match="source_sha256"):
        engine.evaluate(plan([char()], sha=""), actor="K M")


def test_an_outcome_without_evidence_of_what_happened_is_refused(tmp_path):
    store = model.DecisionStore(tmp_path / "d.jsonl")
    decision = rec(store, plan([char(checks=[chk()])]))
    ledger = tmp_path / "o.jsonl"
    with pytest.raises(ValueError, match="evidence of what actually happened"):
        outcome.record(ledger, decision=decision, verdict="CONFIRMED", actual="  ", actor="QA")
    with pytest.raises(ValueError, match="named actor"):
        outcome.record(ledger, decision=decision, verdict="CONFIRMED", actual="measured", actor="")


def test_an_outcome_against_an_unstored_decision_is_refused(tmp_path):
    ledger = tmp_path / "o.jsonl"
    with pytest.raises(ValueError, match="no hash"):
        outcome.record(ledger, decision={"decision_id": "d-x"}, verdict="CONFIRMED",
                       actual="measured", actor="QA")


# ------------------------------------------------------------------------- identity
def test_the_same_plan_ruled_the_same_way_keeps_its_id():
    a = engine.evaluate(plan([char(checks=[chk()])]), actor="A")
    b = engine.evaluate(plan([char(checks=[chk()])]), actor="B")
    assert a.decision_id == b.decision_id
    assert a.hash != b.hash or a.hash == ""   # chain position and time are not the id


def test_a_changed_plan_gets_a_different_id():
    a = engine.evaluate(plan([char(checks=[chk()])]), actor="A")
    b = engine.evaluate(plan([char(checks=[chk(result="failed", actual="100.4")])]), actor="A")
    assert a.decision_id != b.decision_id


# ----------------------------------------------------------------------- confidence
def test_confidence_is_withheld_below_five_outcomes():
    d = engine.evaluate(plan([char(checks=[chk()])]), actor="A",
                        summary={"decision": {"confirmed": 4, "overturned": 0}})
    assert d.confidence.method == "weakest_link"
    assert d.confidence.calibrated is None
    assert "withheld" in d.confidence.note


def test_confidence_is_calibrated_once_five_outcomes_exist():
    d = engine.evaluate(plan([char(checks=[chk()])]), actor="A",
                        summary={"decision": {"confirmed": 4, "overturned": 1}})
    assert d.confidence.method == "calibrated"
    assert d.confidence.calibrated == round(5 / 7, 4)
    assert d.confidence.n == 5


def test_the_floor_is_the_weakest_supporting_node():
    d = engine.evaluate(plan([char(checks=[chk()])]), actor="A",
                        sources={"readings": {"CH-001": {"ocr_engine_a": "100 +/-0.1"}}})
    assert d.confidence.floor == 0.6
    assert d.confidence.bounded_by == "ev:src:ocr_engine_a:CH-001"


# ---------------------------------------------------------------------- the chain
def test_records_chain_and_verify_clean(tmp_path):
    store = model.DecisionStore(tmp_path / "d.jsonl")
    first = store.append(engine.evaluate(plan([char(checks=[chk()])]), actor="A"))
    second = store.append(engine.evaluate(plan([char(limits=None)]), actor="A"))
    assert first.prev_hash == model.GENESIS
    assert second.prev_hash == first.hash
    assert store.verify() == []
    assert store.verify(plan_sha256=SHA) == []
    assert store.verify(plan_sha256="b" * 64) != []


def test_editing_a_stored_decision_breaks_the_chain(tmp_path):
    path = tmp_path / "d.jsonl"
    store = model.DecisionStore(path)
    rec(store, plan([char(checks=[chk()])]))
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    row["actor"] = "someone else"          # the ruling stays, the responsibility does not
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    defects = store.verify()
    assert any("does not recompute" in d for d in defects)


# ------------------------------------------------------------------------ outcomes
def test_an_overturn_is_charged_to_the_weakest_decisive_node(tmp_path):
    store = model.DecisionStore(tmp_path / "d.jsonl")
    decision = rec(store, plan([char(checks=[chk()])]))
    entry = outcome.record(tmp_path / "o.jsonl", decision=decision, verdict="OVERTURNED",
                           actual="the part measured 100.42", actor="QA")
    assert entry["attributed"]["nid"] == "ev:geometry_cross_check:CH-001"
    assert entry["attributed"]["kind"] == "geometry_cross_check"
    assert entry["attributed"]["how"] == "inferred"


def test_a_named_node_that_is_not_in_the_record_is_refused(tmp_path):
    store = model.DecisionStore(tmp_path / "d.jsonl")
    decision = rec(store, plan([char(checks=[chk()])]))
    with pytest.raises(ValueError, match="not an evidence node"):
        outcome.record(tmp_path / "o.jsonl", decision=decision, verdict="OVERTURNED",
                       actual="measured 100.42", actor="QA", blame="ev:nope")


def test_a_confirmed_outcome_credits_the_kinds_that_supported_it(tmp_path):
    store = model.DecisionStore(tmp_path / "d.jsonl")
    decision = rec(store, plan([char(checks=[chk()])]))
    entry = outcome.record(tmp_path / "o.jsonl", decision=decision, verdict="CONFIRMED",
                           actual="part measured 100.01, inside limits", actor="QA")
    assert entry["credited"] == ["geometry_cross_check"]
    assert entry["attributed"]["kind"] is None


def test_reliability_moves_only_after_three_outcomes(tmp_path):
    store = model.DecisionStore(tmp_path / "d.jsonl")
    decision = rec(store, plan([char(checks=[chk()])]))
    ledger = tmp_path / "o.jsonl"
    for i in range(2):
        outcome.record(ledger, decision=decision, verdict="OVERTURNED",
                       actual=f"run {i} measured 100.42", actor="QA")
    table = outcome.reliability(ledger, model.EVIDENCE_WEIGHTS)
    assert table["geometry_cross_check"]["used"] is False
    assert table["geometry_cross_check"]["n"] == 2
    outcome.record(ledger, decision=decision, verdict="OVERTURNED",
                   actual="third run measured 100.43", actor="QA")
    table = outcome.reliability(ledger, model.EVIDENCE_WEIGHTS)
    assert table["geometry_cross_check"]["used"] is True
    assert table["geometry_cross_check"]["mean"] < model.EVIDENCE_WEIGHTS["geometry_cross_check"]
    assert table["human_review"]["used"] is False
    assert outcome.overrides(table)["geometry_cross_check"] < 0.9


def test_a_learned_weight_changes_the_next_ruling(tmp_path):
    store = model.DecisionStore(tmp_path / "d.jsonl")
    decision = rec(store, plan([char(checks=[chk()])]))
    ledger = tmp_path / "o.jsonl"
    for i in range(3):
        outcome.record(ledger, decision=decision, verdict="OVERTURNED",
                       actual=f"run {i} measured 100.42", actor="QA")
    over = outcome.overrides(outcome.reliability(ledger, model.EVIDENCE_WEIGHTS))
    d = engine.evaluate(plan([char(checks=[chk()])]), actor="A", store_reliability=over)
    assert d.evidence[0].weight < 0.9
    assert d.policy["evidence_weights"]["geometry_cross_check"] < 0.9


def test_an_outcome_pointing_at_a_missing_decision_is_reported(tmp_path):
    store_path = tmp_path / "d.jsonl"
    store = model.DecisionStore(store_path)
    rec(store, plan([char(checks=[chk()])]))
    ledger = tmp_path / "o.jsonl"
    ledger.write_text(json.dumps({"decision_hash": "f" * 64, "decision_id": "d-ghost"}) + "\n",
                      encoding="utf-8")
    defects = outcome.dangling(ledger, store_path)
    assert len(defects) == 1 and "not in the store" in defects[0]


# -------------------------------------------------------------------------- report
def test_the_report_carries_the_ruling_the_reasons_and_the_chain(tmp_path):
    store = model.DecisionStore(tmp_path / "d.jsonl")
    decision = rec(store, plan([char(limits=None)]))
    text = report.render(decision)
    assert decision["disposition"] in text
    assert "no_limits" in text
    assert decision["hash"] in text
    assert "Rulings not taken" in text
    assert "Smallest change" in text


# ----------------------------------------------------------------------------- cli
def test_cli_decide_verify_and_tamper(tmp_path):
    plan_path = tmp_path / "p.plan.json"
    plan_path.write_text(json.dumps(plan([char(checks=[chk()])])), encoding="utf-8")
    store = tmp_path / "d.jsonl"
    assert main(["decide", str(plan_path), "--actor", "K M", "--store", str(store)]) == 0
    assert store.exists()
    assert main(["verify", str(store), "--plan", str(plan_path)]) == 0
    row = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
    row["items"][0]["disposition"] = "ACCEPT" if row["items"][0]["disposition"] != "ACCEPT" \
        else "REJECT"
    store.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert main(["verify", str(store)]) == 4


def test_cli_refuses_a_plan_that_is_not_there(tmp_path):
    assert main(["decide", str(tmp_path / "nope.json"), "--actor", "K M"]) == 2


def test_cli_decide_is_idempotent_on_an_unchanged_plan(tmp_path, capsys):
    plan_path = tmp_path / "p.plan.json"
    plan_path.write_text(json.dumps(plan([char(checks=[chk()])])), encoding="utf-8")
    store = tmp_path / "d.jsonl"
    assert main(["decide", str(plan_path), "--actor", "K M", "--store", str(store)]) == 0
    assert main(["decide", str(plan_path), "--actor", "K M", "--store", str(store)]) == 0
    assert len(model.DecisionStore(store).records()) == 1
    assert "unchanged" in capsys.readouterr().out


def test_cli_outcome_then_reliability(tmp_path):
    plan_path = tmp_path / "p.plan.json"
    plan_path.write_text(json.dumps(plan([char(checks=[chk()])])), encoding="utf-8")
    store = tmp_path / "d.jsonl"
    assert main(["decide", str(plan_path), "--actor", "K M", "--store", str(store)]) == 0
    assert main(["outcome", str(store), "--verdict", "OVERTURNED",
                 "--actual", "part measured 100.42", "--actor", "QA"]) == 0
    ledger = store.with_suffix(".outcomes.jsonl")
    assert ledger.exists()
    assert main(["reliability", str(ledger)]) == 0


def test_the_demo_page_says_out_loud_that_a_rehearsal_is_not_a_result(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "decide_demo", Path(__file__).resolve().parents[1] / "tools" / "decide_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    store = model.DecisionStore(tmp_path / "d.jsonl")
    decision = rec(store, plan([char(limits=None), char("CH-002", checks=[chk()])]))
    kinds = [(0.7, {"kind": "source_parse", "mean": 0.7556, "n": 5, "used": True})]
    summary = {"entries": 5, "decision": {"confirmed": 4, "overturned": 1}}
    page = mod.build_page(decision, decision, kinds, summary, "p.plan.json")
    assert page.startswith("<!DOCTYPE html>")
    assert decision["disposition"] in page
    assert "They are synthetic: no part was measured" in page
    assert "Rehearsal - not a result" in page
    assert decision["hash"] in page


# ------------------------------------------------------------------- end to end
def test_a_real_plan_is_ruled_and_the_ruling_reproduces(tmp_path):
    from astracad_web.server import Workspace

    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"])
    r = w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    plan_obj = json.loads(Path(w.resolve_key(r["key"])).read_text(encoding="utf-8"))
    d = engine.evaluate(plan_obj, actor="K M")
    assert d.disposition in model.DISPOSITIONS
    assert d.subject["sha256"] == plan_obj["source_sha256"]
    assert d.subject["characteristics"] == len(plan_obj["characteristics"])
    assert d.evidence
    if d.disposition == model.RELEASE:
        assert plan_obj["approvable"]
    assert engine.evaluate(plan_obj, actor="K M").decision_id == d.decision_id