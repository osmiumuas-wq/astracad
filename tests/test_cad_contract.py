import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from astracad_cad.adapters import ALL, AutoCadAdapter, CreoAdapter, FusionAdapter, SolidWorksAdapter
from astracad_cad.adapters.solidworks import SW_EXE, validate_spec
from astracad_cad.contract import (RISK, Authorization, ConnectionState, Op, OpStatus, Risk,
                                   Support)
from astracad_cad.orchestrator import CadRequest, Orchestrator
from astracad_cad.probes import FakeProbe
from astracad_cad.step_check import read_step
from astracad_core.tasks import TaskState, TaskStore

STEP = Path(__file__).parent / "fixtures" / "step"
MAC = ["mac.exe"]
SW_KEY = r"HKLM\SOFTWARE\SolidWorks"
DISCOVER_OK = ("PROCESSES  SLDWORKS pids: 4242\nSESSION    pid=4242 base=sw2026_SP04 "
               "sp=d260814.002 open_docs=1 started_by_us=False\n")
PRISM = json.loads((STEP / "prism.report.json").read_text())["spec"]


def prism_spec(name="prism"):
    return {"name": name, "units": "mm", "features": [
        {"$op": "sketch.rectangle", "id": "outline", "sketched_on": "front",
         "args": {"width": 60, "height": 40}},
        {"$op": "feature.extrude", "id": "body", "args": {"depth": 20}}],
        "exports": [{"format": "step"}]}


def fake_mac(replay: str, exit_code: int = 0, discover: str = DISCOVER_OK):
    """Responder replaying a real SOLIDWORKS run: copies its report and STEP into --out."""
    def respond(argv):
        if argv[1] == "discover":
            return subprocess.CompletedProcess(argv, 0 if "SESSION" in discover else 1, discover, "")
        if argv[1] == "docs":
            return subprocess.CompletedProcess(argv, 0, "DOCS       2 open: Part1, prism\n", "")
        if argv[1] == "run":
            spec = json.loads(Path(argv[2]).read_text())
            out = Path(argv[argv.index("--out") + 1])
            rep = json.loads((STEP / f"{replay}.report.json").read_text())
            step = out / f"{spec['name']}.step"
            shutil.copy(STEP / f"{replay}.step", step)
            rep["execution"]["artifacts"] = [str(step)]
            (out / f"{spec['name']}.report.json").write_text(json.dumps(rep))
            return subprocess.CompletedProcess(argv, exit_code, "PLAN ok\nREPORT written\n", "")
        raise AssertionError(argv)
    return respond


def sw_probe(responder=None, running=True, installed=True):
    return FakeProbe(platform="win32",
                     subkeys={SW_KEY: ["SOLIDWORKS 2026", "General"]} if installed else {},
                     files={SW_EXE} if installed else set(),
                     processes={"sldworks.exe": [4242]} if running else {},
                     responder=responder)


# ---- connection states ------------------------------------------------------------------------
def test_never_connected_without_a_live_check():
    a = SolidWorksAdapter(sw_probe(responder=None), mac=MAC)
    s = a.status()
    assert s.state is ConnectionState.FAILED          # discover could not run -> not CONNECTED


def test_state_ladder():
    assert SolidWorksAdapter(FakeProbe(platform="linux")).status().state is ConnectionState.NOT_INSTALLED
    assert SolidWorksAdapter(sw_probe(installed=False), mac=MAC).status().state is ConnectionState.NOT_INSTALLED
    no_mac = SolidWorksAdapter(sw_probe())
    assert no_mac.mac_command() is None
    assert no_mac.status().state is ConnectionState.API_UNAVAILABLE
    assert SolidWorksAdapter(sw_probe(running=False), mac=MAC).status().state is ConnectionState.NOT_CONNECTED
    booting = SolidWorksAdapter(sw_probe(fake_mac("prism", discover="ROT no automation object")), mac=MAC)
    assert booting.status().state is ConnectionState.NOT_CONNECTED
    live = SolidWorksAdapter(sw_probe(fake_mac("prism")), mac=MAC).status()
    assert live.state is ConnectionState.CONNECTED and live.version == "sw2026_SP04"


@pytest.mark.parametrize("cls", [CreoAdapter, FusionAdapter, AutoCadAdapter])
def test_discovery_only_adapters_never_connect_and_refuse_operations(cls):
    a = cls(FakeProbe(platform="win32"))
    assert a.status().state is ConnectionState.NOT_INSTALLED
    for op in Op:
        if op is Op.GET_APPLICATION_STATUS:
            continue
        r = a.execute(op, {})
        assert r.status is OpStatus.UNSUPPORTED_OPERATION and r.errors


def test_installed_creo_is_api_unavailable_not_connected():
    pat = r"C:\Program Files\PTC\Creo *\Parametric\bin\parametric.exe"
    p = FakeProbe(platform="win32", globs={pat: [r"C:\Program Files\PTC\Creo 11.0.2.0\Parametric\bin\parametric.exe"]})
    s = CreoAdapter(p).status()
    assert s.state is ConnectionState.API_UNAVAILABLE and s.version == "11.0.2.0"


def test_every_declared_operation_has_a_handler():
    for key, cls in ALL.items():
        a = cls(FakeProbe(platform="win32"))
        d = a.declaration()
        assert set(d.operations) == set(Op), key
        for op, sup in d.operations.items():
            if sup.support is not Support.UNSUPPORTED:
                assert hasattr(a, f"_op_{op.value}"), (key, op)


def test_risk_table_covers_every_operation():
    assert set(RISK) == set(Op)


# ---- spec validation ---------------------------------------------------------------------------
def test_spec_validation_rejects_before_cad():
    assert validate_spec(prism_spec()) == []
    bad = prism_spec("../evil")
    bad["units"] = "in"
    bad["features"].append({"$op": "feature.shell", "id": "body"})
    errs = validate_spec(bad)
    assert any("name" in e for e in errs) and any("units" in e for e in errs)
    assert any("feature.shell" in e for e in errs) and any("duplicated" in e for e in errs)


# ---- create / verify against replayed real runs ------------------------------------------------
def test_create_feature_success_is_verified_by_independent_step_reread(tmp_path):
    a = SolidWorksAdapter(sw_probe(fake_mac("prism")), mac=MAC)
    r = a.execute(Op.CREATE_FEATURE, {"spec": prism_spec(), "out_dir": str(tmp_path),
                                      "expect": {"size_mm": [60, 40, 20], "volume_mm3": 48000}})
    assert r.status is OpStatus.SUCCEEDED, [c for c in r.checks if c.outcome != "passed"]
    names = {c.name for c in r.checks}
    assert {"step:prism.step:volume_vs_expected", "step:prism.step:volume_vs_kernel",
            "step:prism.step:size_mm", "kernel:volume_mm3"} <= names
    assert r.artifacts and len(r.artifacts[0]["sha256"]) == 64
    assert r.data["argv"][:2] == ["mac.exe", "run"]


def test_kernel_failures_are_surfaced_not_accepted(tmp_path):
    a = SolidWorksAdapter(sw_probe(fake_mac("prism_hole", exit_code=1)), mac=MAC)
    spec = prism_spec("prism_hole")
    r = a.execute(Op.CREATE_FEATURE, {"spec": spec, "out_dir": str(tmp_path)})
    assert r.status is OpStatus.EXECUTED_UNVERIFIED
    failed = {c.name for c in r.checks if c.outcome == "failed"}
    assert {"kernel:volume_mm3", "kernel:surface_area_mm2"} <= failed


def test_wrong_size_request_fails_step_check(tmp_path):
    a = SolidWorksAdapter(sw_probe(fake_mac("prism")), mac=MAC)
    r = a.execute(Op.CREATE_FEATURE, {"spec": prism_spec(), "out_dir": str(tmp_path),
                                      "expect": {"size_mm": [60, 40, 25]}})
    assert r.status is OpStatus.EXECUTED_UNVERIFIED
    assert any(c.name.endswith("size_mm") and c.outcome == "failed" for c in r.checks)


def test_not_connected_stops_before_writing(tmp_path):
    a = SolidWorksAdapter(sw_probe(fake_mac("prism"), running=False), mac=MAC)
    r = a.execute(Op.CREATE_FEATURE, {"spec": prism_spec(), "out_dir": str(tmp_path / "o")})
    assert r.status is OpStatus.NOT_CONNECTED and not (tmp_path / "o").exists()


def test_modify_appends_parameter_step(tmp_path):
    probe = sw_probe(fake_mac("prism"))
    a = SolidWorksAdapter(probe, mac=MAC)
    a.execute(Op.MODIFY_FEATURE, {"base_spec": prism_spec(), "target": "D1@Boss-Extrude1",
                                  "value": 30, "out_dir": str(tmp_path), "name": "prism"})
    spec = json.loads((tmp_path / "prism.spec.json").read_text())
    assert spec["features"][-1] == {"$op": "feature.parameter", "id": "modify",
                                    "args": {"target": "D1@Boss-Extrude1", "value": 30}}


def test_unsupported_operation_is_refused_not_substituted():
    # open_document became supported on 2026-10-17 (mac inspect); assemblies still are not
    a = SolidWorksAdapter(sw_probe(fake_mac("prism")), mac=MAC)
    for op in (Op.MODIFY_ASSEMBLY, Op.INSPECT_ASSEMBLY, Op.EXPORT_DOCUMENT):
        r = a.execute(op, {"path": "shaft.sldasm"})
        assert r.status is OpStatus.UNSUPPORTED_OPERATION
    assert not any(c[1] in ("run", "edit") for c in a.probe.calls)


def test_list_open_documents():
    r = SolidWorksAdapter(sw_probe(fake_mac("prism")), mac=MAC).execute(Op.LIST_OPEN_DOCUMENTS, {})
    assert r.ok and r.data["titles"] == ["Part1", "prism"]


# ---- STEP re-reader on real SOLIDWORKS output --------------------------------------------------
def test_step_reread_real_prism():
    r = read_step(STEP / "prism.step")
    assert r.originating_system == "SolidWorks 2026" and r.length_unit == "mm"
    assert r.solids == 1 and r.faces == 6
    assert r.size_mm == pytest.approx([60, 40, 20], abs=1e-9)
    assert r.volume_mm3 == pytest.approx(48000.0, rel=1e-12)


def test_step_reread_refuses_curved_volume():
    r = read_step(STEP / "prism_hole.step")
    assert r.volume_mm3 is None and "CYLINDRICAL_SURFACE" in r.volume_reason
    assert r.size_mm == pytest.approx([60, 40, 20], abs=1e-4)


def test_step_reread_rejects_junk(tmp_path):
    p = tmp_path / "x.step"
    p.write_text("not a step file")
    assert read_step(p).errors


def test_step_inch_unit_scales(tmp_path):
    txt = (STEP / "prism.step").read_text(encoding="latin-1")
    txt = txt.replace("( LENGTH_UNIT ( ) NAMED_UNIT ( * ) SI_UNIT ( .MILLI., .METRE. ) )",
                      "( CONVERSION_BASED_UNIT ( 'INCH', #9 ) LENGTH_UNIT ( ) NAMED_UNIT ( * ) )")
    p = tmp_path / "inch.step"
    p.write_text(txt, encoding="latin-1")
    r = read_step(p)
    assert r.length_unit == "inch"
    assert r.size_mm == pytest.approx([60 * 25.4, 40 * 25.4, 20 * 25.4])


# ---- orchestrator / permission gate ------------------------------------------------------------
def orch(tmp_path, probe):
    return Orchestrator({"solidworks": SolidWorksAdapter(probe, mac=MAC)},
                        TaskStore(tmp_path / "t.db"))


def test_mutation_without_authorization_waits(tmp_path):
    o = orch(tmp_path, sw_probe(fake_mac("prism")))
    req = CadRequest("solidworks", Op.CREATE_FEATURE,
                     {"spec": prism_spec(), "out_dir": str(tmp_path / "out")})
    tid, res = o.submit(req, None)
    assert res.status is OpStatus.REQUIRES_AUTHORIZATION
    assert o.store.get(tid).state is TaskState.WAITING_FOR_APPROVAL
    assert not (tmp_path / "out").exists()


def test_output_outside_authorised_dirs_is_refused(tmp_path):
    o = orch(tmp_path, sw_probe(fake_mac("prism")))
    auth = Authorization("KM", frozenset({Op.CREATE_FEATURE}), (str(tmp_path / "allowed"),))
    tid, res = o.submit(CadRequest("solidworks", Op.CREATE_FEATURE,
                                   {"spec": prism_spec(), "out_dir": str(tmp_path / "elsewhere")}),
                        auth)
    assert res.status is OpStatus.REQUIRES_AUTHORIZATION and "outside" in res.message


def test_authorised_run_records_full_evidence_and_resume(tmp_path):
    o = orch(tmp_path, sw_probe(fake_mac("prism")))
    out = tmp_path / "out"
    req = CadRequest("solidworks", Op.CREATE_FEATURE,
                     {"spec": prism_spec(), "out_dir": str(out),
                      "expect": {"size_mm": [60, 40, 20], "volume_mm3": 48000}})
    tid, _ = o.submit(req, None)
    res = o.resume(tid, req, Authorization("KM", frozenset({Op.CREATE_FEATURE}), (str(out),)))
    t = o.store.get(tid)
    assert res.ok and t.state is TaskState.SUCCEEDED
    assert t.of_kind("approval")[0]["approver"] == "KM"
    assert t.of_kind("artifact") and t.of_kind("tool_call")
    assert all(v["outcome"] == "passed" for v in t.of_kind("verification"))


def test_unverified_run_ends_failed(tmp_path):
    o = orch(tmp_path, sw_probe(fake_mac("prism_hole", exit_code=1)))
    out = tmp_path / "o"
    tid, res = o.submit(CadRequest("solidworks", Op.CREATE_FEATURE,
                                   {"spec": prism_spec("prism_hole"), "out_dir": str(out)}),
                        Authorization("KM", frozenset({Op.CREATE_FEATURE}), (str(out),)))
    assert o.store.get(tid).state is TaskState.FAILED
    assert o.store.get(tid).outcome == "EXECUTED_UNVERIFIED"


def test_read_only_status_needs_no_authorization(tmp_path):
    o = orch(tmp_path, sw_probe(fake_mac("prism")))
    tid, res = o.submit(CadRequest("solidworks", Op.GET_APPLICATION_STATUS, {}))
    assert res.ok and o.store.get(tid).state is TaskState.SUCCEEDED


# ---- regressions from the first real SOLIDWORKS run (2026-10-17) -------------------------------
def shaft_spec():
    return {"name": "shaft_d25_l100", "units": "mm", "features": [
        {"$op": "sketch.circle", "id": "disc", "sketched_on": "front",
         "args": {"radius": 12.5, "x": 0, "y": 0}},
        {"$op": "feature.extrude", "id": "body", "args": {"depth": 100}}],
        "exports": [{"format": "step"}]}


def test_real_shaft_step_box_is_exact_for_split_arcs():
    r = read_step(STEP / "shaft_d25_l100.step")
    assert r.edge_curve_types.get("CIRCLE") == 4          # SOLIDWORKS writes each end as 2 arcs
    assert not r.bbox_approximate
    assert r.size_mm == pytest.approx([25, 25, 100], abs=1e-9)


def test_real_block_from_adapter_run():
    r = read_step(STEP / "block_60x40x20.step")
    assert r.volume_mm3 == pytest.approx(48000.0, rel=1e-12)


def test_cylinder_run_with_not_verifiable_volume_ends_succeeded_not_stuck(tmp_path):
    """Live defect: the adapter accepted not_verifiable, the task store did not, and the task was
    left RUNNING after an exception. Both now agree: no failed check + at least one passed."""
    o = orch(tmp_path, sw_probe(fake_mac("shaft_d25_l100")))
    out = tmp_path / "o"
    expect = {"size_mm": [25, 25, 100], "volume_mm3": math.pi * 12.5 ** 2 * 100}
    tid, res = o.submit(CadRequest("solidworks", Op.CREATE_FEATURE,
                                   {"spec": shaft_spec(), "out_dir": str(out), "expect": expect}),
                        Authorization("KM", frozenset({Op.CREATE_FEATURE}), (str(out),)))
    t = o.store.get(tid)
    assert res.status is OpStatus.SUCCEEDED and t.state is TaskState.SUCCEEDED
    outcomes = {v["name"]: v["outcome"] for v in t.of_kind("verification")}
    assert outcomes["step:shaft_d25_l100.step:volume_vs_expected"] == "not_verifiable"
    assert outcomes["step:shaft_d25_l100.step:size_mm"] == "passed"


def test_finalisation_error_never_leaves_task_running(tmp_path):
    from astracad_cad.contract import Check, OperationResult

    class OnlyUnverifiable(SolidWorksAdapter):
        def _op_get_application_status(self, params):
            return OperationResult("solidworks", Op.GET_APPLICATION_STATUS, OpStatus.SUCCEEDED,
                                   "x", checks=[Check("c", "not_verifiable", "-", "-")])
    o = Orchestrator({"solidworks": OnlyUnverifiable(sw_probe(fake_mac("prism")), mac=MAC)},
                     TaskStore(tmp_path / "t.db"))
    tid, _ = o.submit(CadRequest("solidworks", Op.GET_APPLICATION_STATUS, {}))
    t = o.store.get(tid)
    assert t.state is TaskState.FAILED and t.outcome == "FINALISE_ERROR"


def test_existing_outputs_are_never_overwritten_silently(tmp_path):
    """Live defect: two different requests mapped to the same name and the second run replaced
    the first part's files."""
    a = SolidWorksAdapter(sw_probe(fake_mac("prism")), mac=MAC)
    first = a.execute(Op.CREATE_FEATURE, {"spec": prism_spec(), "out_dir": str(tmp_path)})
    assert first.ok
    before = (tmp_path / "prism.step").read_bytes()
    again = a.execute(Op.CREATE_FEATURE, {"spec": prism_spec(), "out_dir": str(tmp_path)})
    assert again.status is OpStatus.INVALID_REQUEST and again.errors
    assert (tmp_path / "prism.step").read_bytes() == before
    forced = a.execute(Op.CREATE_FEATURE, {"spec": prism_spec(), "out_dir": str(tmp_path),
                                           "overwrite": True})
    assert forced.ok


def test_hole_and_plain_block_get_distinct_names():
    from astracad_core.requests import interpret
    a = interpret("Create a 60 x 40 x 20 mm block in SolidWorks and export STEP")
    b = interpret("Create a 60 x 40 x 20 mm block in SolidWorks with a 10 mm hole at the centre")
    assert a.spec["name"] != b.spec["name"]
