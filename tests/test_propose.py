"""Word -> candidate dimensions: a proposal the user must pick from, never an auto-selection."""
from astracad_cad.contract import OpStatus
from astracad_cad.orchestrator import Orchestrator
from astracad_cad.propose import proposal_route, propose_dimensions, propose_for, short_name
from astracad_core.requests import interpret
from astracad_core.tasks import TaskStore

from test_cad_edit import adapter, source  # noqa: F401  (fixture)

DATA = {"dimensions": [{"Name": "D1@Boss-Extrude1@shaft.Part", "ValueMm": 100},
                       {"Name": "D1@Hole1@shaft.Part", "ValueMm": 8},
                       {"Name": "D2@Sketch1@shaft.Part", "ValueMm": 25}]}


def test_short_name_strips_part_suffix_only():
    assert short_name("D1@Boss-Extrude1@shaft.Part") == "D1@Boss-Extrude1"
    assert short_name("D1@Sketch1") == "D1@Sketch1"


def test_every_dimension_is_a_candidate_and_none_is_selected():
    p = propose_dimensions("diameter", 25.0, DATA, "C:/p/shaft.SLDPRT")
    assert p["auto_selected"] is False and "NOT MADE" in p["selection"]
    assert {c["name"] for c in p["candidates"]} == {"D1@Boss-Extrude1", "D1@Hole1", "D2@Sketch1"}
    assert p["candidates"][0]["name"] == "D1@Hole1" and "AI_PROPOSED" in p["candidates"][0]["hint"]
    assert all("--set " + c["name"] + "=25" in c["command"] for c in p["candidates"])
    assert [c["already_at_value"] for c in p["candidates"] if c["name"] == "D2@Sketch1"] == [True]


def test_single_candidate_is_still_not_selected():
    p = propose_dimensions("length", 120.0, {"dimensions": DATA["dimensions"][:1]}, "x.SLDPRT")
    assert len(p["candidates"]) == 1 and p["auto_selected"] is False


def test_no_dimensions_proposes_nothing():
    p = propose_dimensions("diameter", 25.0, {"dimensions": []}, "x.SLDPRT")
    assert p["candidates"] == [] and "nothing can be proposed" in p["selection"]


def test_word_request_with_path_only_misses_the_dimension_name():
    it = interpret('change the diameter of "C:/parts/shaft.SLDPRT" to 25 mm')
    assert it.intent == "modify_existing" and not it.executable and it.spec is None
    assert it.fact("document") == "C:/parts/shaft.SLDPRT" and it.application == "solidworks"
    assert len(it.missing) == 1 and proposal_route(it)


def test_word_request_without_path_is_not_routed_to_proposal():
    it = interpret("Open my SolidWorks shaft model, change its diameter to 25 mm")
    assert not proposal_route(it)


def test_proposal_inspects_read_only_and_records_a_task(source, tmp_path):  # noqa: F811
    it = interpret(f'set the length of "{source}" to 35 mm')
    a = adapter()
    o = Orchestrator({"solidworks": a}, TaskStore(tmp_path / "t.db"))
    tid, res, prop = propose_for(it, o)
    assert res.status is OpStatus.SUCCEEDED and tid
    assert prop["candidates"][0]["name"] == "D1@Boss-Extrude1"
    assert prop["candidates"][0]["proposed_mm"] == 35.0
    assert not any(c[1] == "edit" for c in a.probe.calls)


KINDED = {"dimensions": [{"Name": "D1@Boss-Extrude1@s.Part", "ValueMm": 100, "Kind": "linear"},
                         {"Name": "D1@Sketch1@s.Part", "ValueMm": 25, "Kind": "diameter"},
                         {"Name": "D1@Fillet1@s.Part", "ValueMm": 2, "Kind": "radius"}]}


def test_kernel_kind_orders_but_never_selects():
    p = propose_dimensions("diameter", 30.0, KINDED, "s.SLDPRT")
    assert p["auto_selected"] is False and len(p["candidates"]) == 3
    assert [c["kind_match"] for c in p["candidates"]] == ["match", "match", "mismatch"]
    assert p["candidates"][-1]["name"] == "D1@Boss-Extrude1"
    assert all(c["hint"] is None for c in p["candidates"])   # kind present -> no name guess


def test_diameter_on_radius_dimension_is_converted_and_marked():
    c = {c["name"]: c for c in propose_dimensions("diameter", 30.0, KINDED, "s")["candidates"]}
    assert c["D1@Fillet1"]["proposed_mm"] == 15.0 and "CALCULATED" in c["D1@Fillet1"]["conversion"]
    assert "--set D1@Fillet1=15 " in c["D1@Fillet1"]["command"]
    assert c["D1@Sketch1"]["proposed_mm"] == 30.0 and "conversion" not in c["D1@Sketch1"]


def test_length_word_prefers_linear():
    p = propose_dimensions("length", 120.0, KINDED, "s")
    assert p["candidates"][0]["name"] == "D1@Boss-Extrude1"
    assert p["candidates"][0]["kind_match"] == "match"
