"""Editing existing parts: the source must never change, values are read back, STEP re-read."""
import json
import subprocess
from pathlib import Path

import pytest

from astracad_cad.adapters.solidworks import SW_EXE, SolidWorksAdapter
from astracad_cad.contract import Authorization, Op, OpStatus
from astracad_cad.orchestrator import CadRequest, Orchestrator
from astracad_cad.probes import FakeProbe
from astracad_core.requests import interpret
from astracad_core.tasks import TaskState, TaskStore

STEP = Path(__file__).parent / "fixtures" / "step"
DISCOVER = "SESSION    pid=1 base=sw2026_SP04 sp=x open_docs=0 started_by_us=False\n"


def deeper_step(depth: float) -> str:
    """The real 60x40x20 SOLIDWORKS export with its top face moved to z=depth."""
    txt = (STEP / "prism.step").read_text(encoding="latin-1")
    return txt.replace("20.00000000000000000 ) )", f"{depth:.17f} ) )")


def fake(tamper=False, missing_dim=False, after=35.0):
    def respond(argv):
        cmd = argv[1]
        if cmd == "discover":
            return subprocess.CompletedProcess(argv, 0, DISCOVER, "")
        if cmd == "inspect":
            rep = {"command": "inspect", "dimensions": [{"Name": "D1@Boss-Extrude1@block.Part",
                                                          "ValueMm": 20.0}],
                   "mass": {"volume_mm3": 48000.0}, "source_unchanged": True}
            return subprocess.CompletedProcess(argv, 0, json.dumps(rep), "")
        if cmd == "edit":
            src = Path(argv[2])
            out = Path(argv[argv.index("--out") + 1])
            name = argv[argv.index("--name") + 1]
            if tamper:
                src.write_bytes(src.read_bytes() + b"x")
            sets = [argv[i + 1] for i, a in enumerate(argv) if a == "--set"]
            edits, failures, arts = [], [], []
            for s in sets:
                k, v = s.split("=")
                if missing_dim:
                    edits.append({"target": k, "requested_mm": float(v), "ok": False})
                    failures.append(f"{k}: no such dimension")
                else:
                    edits.append({"target": k, "requested_mm": float(v),
                                  "resolved": k + "@copy.Part", "before_mm": 20.0,
                                  "after_mm": after, "ok": after == float(v)})
            if not missing_dim:
                (out / f"{name}.SLDPRT").write_bytes(b"part")
                (out / f"{name}.step").write_text(deeper_step(after), encoding="latin-1")
                arts = [{"path": str(out / f"{name}.SLDPRT")}, {"path": str(out / f"{name}.step")}]
            rep = {"command": "edit", "source": {"unchanged": not tamper}, "edits": edits,
                   "whats_wrong_before": 0, "whats_wrong_after": 0,
                   "mass_after": {"volume_mm3": 60 * 40 * after}, "artifacts": arts,
                   "failures": failures, "success": not failures}
            (out / f"{name}.edit.json").write_text(json.dumps(rep), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0 if not failures else 1, "EDIT", "")
        raise AssertionError(argv)
    return respond


def adapter(**kw):
    p = FakeProbe(platform="win32", subkeys={r"HKLM\SOFTWARE\SolidWorks": ["SOLIDWORKS 2026"]},
                  files={SW_EXE}, processes={"sldworks.exe": [1]}, responder=fake(**kw))
    return SolidWorksAdapter(p, mac=["mac.exe"])


@pytest.fixture
def source(tmp_path):
    p = tmp_path / "src" / "block.SLDPRT"
    p.parent.mkdir()
    p.write_bytes(b"original part bytes")
    return p


def params(source, out, **extra):
    d = {"source_path": str(source), "set": {"D1@Boss-Extrude1": 35.0}, "name": "block_rev",
         "out_dir": str(out), "expect": {"size_mm": [60, 40, 35], "volume_mm3": 84000.0}}
    d.update(extra)
    return d


def test_edit_copy_success_with_independent_reread(source, tmp_path):
    before = source.read_bytes()
    r = adapter().execute(Op.MODIFY_FEATURE, params(source, tmp_path / "o"))
    assert r.status is OpStatus.SUCCEEDED, [(c.name, c.outcome, c.actual) for c in r.checks]
    assert source.read_bytes() == before
    names = {c.name: c.outcome for c in r.checks}
    assert names["source_unchanged_python"] == "passed"
    assert names["kernel:dimension:D1@Boss-Extrude1@copy.Part"] == "passed"
    assert names["step:block_rev.step:size_mm"] == "passed"
    assert names["step:block_rev.step:volume_vs_expected"] == "passed"
    assert r.data["argv"][1] == "edit" and "--set" in r.data["argv"]


def test_tampered_source_is_caught(source, tmp_path):
    r = adapter(tamper=True).execute(Op.MODIFY_FEATURE, params(source, tmp_path / "o"))
    assert r.status is OpStatus.EXECUTED_UNVERIFIED
    assert {c.name for c in r.checks if c.outcome == "failed"} >= {"source_unchanged_python",
                                                                     "source_unchanged_mac"}


def test_readback_mismatch_fails(source, tmp_path):
    r = adapter(after=30.0).execute(Op.MODIFY_FEATURE, params(source, tmp_path / "o"))
    assert r.status is OpStatus.FAILED      # the requested value was not achieved


def test_missing_dimension_is_failure_not_substitution(source, tmp_path):
    r = adapter(missing_dim=True).execute(Op.MODIFY_FEATURE, params(source, tmp_path / "o"))
    assert r.status is OpStatus.FAILED and not r.artifacts


@pytest.mark.parametrize("bad", [{"name": "block"}, {"name": "../x"},
                                 {"set": {"diameter": 25}}, {"set": {"D1@Sketch1": -3}},
                                 {"set": {}}])
def test_invalid_edits_never_reach_cad(source, tmp_path, bad):
    a = adapter()
    r = a.execute(Op.MODIFY_FEATURE, params(source, tmp_path / "o", **bad))
    assert r.status is OpStatus.INVALID_REQUEST
    assert not any(c[1] == "edit" for c in a.probe.calls)


def test_existing_output_not_overwritten(source, tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    (out / "block_rev.SLDPRT").write_bytes(b"keep me")
    r = adapter().execute(Op.MODIFY_FEATURE, params(source, out))
    assert r.status is OpStatus.INVALID_REQUEST and (out / "block_rev.SLDPRT").read_bytes() == b"keep me"


def test_inspect_reads_dimensions_and_proves_source_unchanged(source):
    r = adapter().execute(Op.INSPECT_PARAMETERS, {"path": str(source)})
    assert r.ok and r.data["dimensions"][0]["Name"].startswith("D1@Boss-Extrude1")


def test_edit_through_orchestrator_needs_authorization(source, tmp_path):
    o = Orchestrator({"solidworks": adapter()}, TaskStore(tmp_path / "t.db"))
    out = tmp_path / "o"
    tid, res = o.submit(CadRequest("solidworks", Op.MODIFY_FEATURE, params(source, out)))
    assert res.status is OpStatus.REQUIRES_AUTHORIZATION and not out.exists()
    tid2, res2 = o.submit(CadRequest("solidworks", Op.MODIFY_FEATURE, params(source, out)),
                          Authorization("KM", frozenset({Op.MODIFY_FEATURE}), (str(out),)))
    assert res2.ok and o.store.get(tid2).state is TaskState.SUCCEEDED


def test_interpreter_makes_fully_specified_edit_executable():
    it = interpret('change D1@Boss-Extrude1 of "C:\\parts\\shaft.SLDPRT" to 1 in')
    assert it.executable and it.intent == "modify_existing"
    assert it.spec["set"] == {"D1@Boss-Extrude1": 25.4}
    assert it.spec["source_path"] == "C:\\parts\\shaft.SLDPRT" and it.spec["name"] == "shaft_rev"


def test_interpreter_vague_edit_still_lists_missing():
    it = interpret("Open my SolidWorks shaft model, change its diameter to 25 mm")
    assert not it.executable and len(it.missing) == 2


def test_failed_edit_is_failed_even_if_files_were_left():
    """Live run 2026-10-17: an unknown dimension name left an unedited copy + STEP behind and the
    adapter reported EXECUTED_UNVERIFIED. mac now discards the copy; the adapter reports FAILED
    whenever any requested dimension was not set."""
    import tempfile
    d = Path(tempfile.mkdtemp())
    src = d / "block.SLDPRT"
    src.write_bytes(b"x")
    out = d / "o"
    out.mkdir()

    def respond(argv):
        if argv[1] == "discover":
            return subprocess.CompletedProcess(argv, 0, DISCOVER, "")
        (out / "bad.step").write_text(deeper_step(20.0), encoding="latin-1")
        rep = {"source": {"unchanged": True}, "edits": [{"target": "D9@X", "requested_mm": 5,
                                                         "ok": False}],
               "whats_wrong_before": 0, "whats_wrong_after": 0, "mass_after": {},
               "artifacts": [{"path": str(out / "bad.step")}], "failures": ["D9@X: no such"],
               "success": False}
        (out / "bad.edit.json").write_text(json.dumps(rep))
        return subprocess.CompletedProcess(argv, 1, "", "")
    p = FakeProbe(platform="win32", subkeys={r"HKLM\SOFTWARE\SolidWorks": ["SOLIDWORKS 2026"]},
                  files={SW_EXE}, processes={"sldworks.exe": [1]}, responder=respond)
    r = SolidWorksAdapter(p, mac=["mac.exe"]).execute(
        Op.MODIFY_FEATURE, {"source_path": str(src), "set": {"D9@X": 5}, "name": "bad",
                            "out_dir": str(out)})
    assert r.status is OpStatus.FAILED and "nothing was substituted" in r.message


def test_real_edited_parts_reread():
    from astracad_cad.step_check import read_step
    b = read_step(STEP / "block_depth35.step")
    assert b.size_mm == pytest.approx([60, 40, 35], abs=1e-9)
    assert b.volume_mm3 == pytest.approx(84000.0, rel=1e-12)
    s = read_step(STEP / "shaft_l120.step")
    assert s.size_mm == pytest.approx([25, 25, 120], abs=1e-9) and not s.bbox_approximate
    rep = json.loads((STEP / "block_depth35.edit.json").read_text(encoding="utf-8-sig"))
    assert rep["source"]["unchanged"] and rep["edits"][0]["after_mm"] == 35
