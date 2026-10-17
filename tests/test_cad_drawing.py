"""Drawing route: SOLIDWORKS makes the drawing, ASTraCAD's inspection engine independently checks it.

Both DXFs are real SOLIDWORKS 2026 output (see tests/fixtures/sw_drawing/PROVENANCE.md)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from astracad_cad.adapters.solidworks import SW_EXE, SolidWorksAdapter, drawing_dxf_checks
from astracad_cad.contract import RISK, Authorization, Op, OpStatus, Risk
from astracad_cad.orchestrator import CadRequest, Orchestrator
from astracad_cad.probes import FakeProbe
from astracad_core.tasks import TaskStore

SWD = Path(__file__).parent / "fixtures" / "sw_drawing"
REPORT = json.loads((SWD / "block_hole_drw.drawing.json").read_text(encoding="utf-8"))
DISCOVER = "SESSION    pid=1 base=sw2026_SP04 sp=x open_docs=0 started_by_us=False\n"


def fake(dxf_name="block_hole_drw.dxf"):
    def respond(argv):
        if argv[1] == "discover":
            return subprocess.CompletedProcess(argv, 0, DISCOVER, "")
        if argv[1] == "drawing":
            out, name = Path(argv[argv.index("--out") + 1]), argv[argv.index("--name") + 1]
            shutil.copy(SWD / dxf_name, out / f"{name}.dxf")
            (out / f"{name}.SLDDRW").write_bytes(b"drawing")
            (out / f"{name}.pdf").write_bytes(b"%PDF-1.4")
            (out / f"{name}.drawing.json").write_text(json.dumps(REPORT), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "DRAWING ok", "")
        raise AssertionError(argv)
    return respond


def adapter(**kw):
    p = FakeProbe(platform="win32", subkeys={r"HKLM\SOFTWARE\SolidWorks": ["SOLIDWORKS 2026"]},
                  files={SW_EXE}, processes={"sldworks.exe": [1]}, responder=fake(**kw))
    return SolidWorksAdapter(p, mac=["mac.exe"])


@pytest.fixture
def part(tmp_path):
    p = tmp_path / "src" / "block.SLDPRT"
    p.parent.mkdir()
    p.write_bytes(b"part bytes")
    return p


def test_create_drawing_is_persisting_and_needs_authorization(part, tmp_path):
    assert RISK[Op.CREATE_DRAWING] is Risk.PERSISTING
    o = Orchestrator({"solidworks": adapter()}, TaskStore(tmp_path / "t.db"))
    params = {"part_path": str(part), "name": "d", "out_dir": str(tmp_path / "o")}
    _, res = o.submit(CadRequest("solidworks", Op.CREATE_DRAWING, params))
    assert res.status is OpStatus.REQUIRES_AUTHORIZATION and not (tmp_path / "o").exists()
    _, res = o.submit(CadRequest("solidworks", Op.CREATE_DRAWING, params),
                      Authorization("KM", frozenset({Op.CREATE_DRAWING}), (str(tmp_path / "o"),)))
    assert res.status is OpStatus.SUCCEEDED, [(c.name, c.outcome, c.actual) for c in res.checks
                                              if c.outcome != "passed"]


def test_real_mm_drawing_passes_every_independent_check():
    checks = {c.name: c.outcome for c in drawing_dxf_checks(SWD / "block_hole_drw.dxf",
                                                             REPORT["model_dimensions"])}
    assert set(checks.values()) == {"passed"}
    assert {"inspect:printed_value:D1@Sketch1", "inspect:printed_value:D1@Sketch2",
            "inspect:printed_value:D1@Boss-Extrude1", "inspect:no_extra_dimensions"} <= set(checks)


def test_real_inch_template_drawing_is_rejected(part, tmp_path):
    r = adapter(dxf_name="block_hole_drw_inch_template.dxf").execute(
        Op.CREATE_DRAWING, {"part_path": str(part), "name": "d", "out_dir": str(tmp_path / "o")})
    failed = {c.name for c in r.checks if c.outcome == "failed"}
    assert r.status is OpStatus.EXECUTED_UNVERIFIED       # mac said ok; the drawing is not
    assert "inspect:dxf_unit_mm" in failed and "inspect:printed_value:D1@Sketch1" in failed
    assert "inspect:no_extra_dimensions" in failed        # 2.36 etc. and the duplicates


@pytest.mark.parametrize("bad", [{"name": "../x"}, {"angle": "second"},
                                 {"part_path": "C:/nope/x.SLDPRT"}])
def test_invalid_requests_never_reach_cad(part, tmp_path, bad):
    a = adapter()
    params = {"part_path": str(part), "name": "d", "out_dir": str(tmp_path / "o"), **bad}
    assert a.execute(Op.CREATE_DRAWING, params).status is OpStatus.INVALID_REQUEST
    assert not any(c[1] == "drawing" for c in a.probe.calls)


def test_existing_outputs_are_not_overwritten(part, tmp_path):
    out = tmp_path / "o"
    out.mkdir()
    (out / "d.SLDDRW").write_bytes(b"keep")
    r = adapter().execute(Op.CREATE_DRAWING, {"part_path": str(part), "name": "d", "out_dir": str(out)})
    assert r.status is OpStatus.INVALID_REQUEST and (out / "d.SLDDRW").read_bytes() == b"keep"


def test_a_hung_cad_run_becomes_a_failed_result_not_an_exception(part, tmp_path):
    def hang(argv):
        if argv[1] == "discover":
            return subprocess.CompletedProcess(argv, 0, DISCOVER, "")
        raise subprocess.TimeoutExpired(argv, 600)
    p = FakeProbe(platform="win32", subkeys={r"HKLM\SOFTWARE\SolidWorks": ["SOLIDWORKS 2026"]},
                  files={SW_EXE}, processes={"sldworks.exe": [1]}, responder=hang)
    o = Orchestrator({"solidworks": SolidWorksAdapter(p, mac=["mac.exe"])}, TaskStore(tmp_path / "t.db"))
    out = tmp_path / "o"
    tid, res = o.submit(CadRequest("solidworks", Op.CREATE_DRAWING,
                                   {"part_path": str(part), "name": "d", "out_dir": str(out)}),
                        Authorization("KM", frozenset({Op.CREATE_DRAWING}), (str(out),)))
    assert res.status is OpStatus.FAILED and "did not complete" in res.message
    assert o.store.get(tid).state.value == "FAILED"


def test_drawing_flags_forwarded_to_mac(part, tmp_path):
    a = adapter()
    params = {
        "part_path": str(part), "name": "bracket", "out_dir": str(tmp_path / "o"),
        "title": "Custom Bracket", "drawing_number": "DWG-101", "revision": "B",
        "material": "Steel", "tolerance": "ISO 2768-c",
    }
    r = a.execute(Op.CREATE_DRAWING, params)
    assert r.status is OpStatus.SUCCEEDED
    call = next(c for c in a.probe.calls if c[1] == "drawing")
    assert "--title" in call and call[call.index("--title") + 1] == "Custom Bracket"
    assert "--drawing-number" in call and call[call.index("--drawing-number") + 1] == "DWG-101"
    assert "--revision" in call and call[call.index("--revision") + 1] == "B"
    assert "--material" in call and call[call.index("--material") + 1] == "Steel"
    assert "--tolerance" in call and call[call.index("--tolerance") + 1] == "ISO 2768-c"


def test_drawing_dxf_checks_verifies_title_block_and_general_tolerance(tmp_path):
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50), (0, 0)])
    msp.add_linear_dim(base=(50, 60), p1=(0, 0), p2=(100, 0), text="100").render()
    msp.add_text("TITLE: Widget")
    msp.add_text("DRAWING NUMBER: DWG-42")
    msp.add_text("REVISION: B")
    msp.add_text("MATERIAL: Aluminum")
    msp.add_text("NOTES: GENERAL TOLERANCES PER ISO 2768-m")
    dxf = tmp_path / "test.dxf"
    doc.saveas(dxf)

    checks = {c.name: (c.outcome, c.actual) for c in drawing_dxf_checks(
        dxf, [{"name": "D1@Sketch1", "mm": 100.0}],
        expected_title={"drawing_number": "DWG-42", "revision": "B", "material": "Aluminum"},
        expected_tolerance="ISO 2768-m")}

    assert checks["inspect:title_block:drawing_number"][0] == "passed"
    assert checks["inspect:title_block:revision"][0] == "passed"
    assert checks["inspect:title_block:material"][0] == "passed"
    assert checks["inspect:general_tolerance"][0] == "passed"

    # Mismatch is caught
    bad = {c.name: c.outcome for c in drawing_dxf_checks(
        dxf, [{"name": "D1@Sketch1", "mm": 100.0}],
        expected_title={"drawing_number": "WRONG-NO", "revision": "Z"},
        expected_tolerance="ISO 2768-c")}
    assert bad["inspect:title_block:drawing_number"] == "failed"
    assert bad["inspect:title_block:revision"] == "failed"
    assert bad["inspect:general_tolerance"] == "failed"


def test_unstated_revision_and_tolerance_are_reported_not_invented(part, tmp_path, monkeypatch):
    # mac prints REVISION: NOT STATED and adds no tolerance note; the result must say so, never pass it
    monkeypatch.setitem(REPORT, "title_block", {"revision": None})   # fixture DXF has no title block
    monkeypatch.setitem(REPORT, "general_tolerance", None)
    monkeypatch.setitem(REPORT, "missing", ["revision", "general_tolerance"])
    a = adapter()
    r = a.execute(Op.CREATE_DRAWING, {"part_path": str(part), "name": "d", "out_dir": str(tmp_path / "o")})
    call = next(c for c in a.probe.calls if c[1] == "drawing")
    assert "--revision" not in call and "--tolerance" not in call
    checks = {c.name: c.outcome for c in r.checks}
    assert checks["drawing:revision"] == "not_verifiable"
    assert checks["drawing:general_tolerance"] == "not_verifiable"
    assert "inspect:title_block:revision" not in checks and "inspect:general_tolerance" not in checks
    assert r.status is OpStatus.SUCCEEDED, [(c.name, c.outcome, c.actual) for c in r.checks
                                            if c.outcome == "failed"]


def test_title_block_read_from_real_solidworks_note_blocks():
    # Genuine `mac drawing` output: every title-block line sits in an inserted SW_NOTE_n block
    checks = {c.name: (c.outcome, c.actual) for c in drawing_dxf_checks(
        SWD / "pocket_title_block.dxf", [],
        expected_title={"title": "Pocket block", "drawing_number": "DWG-0418", "revision": "B",
                        "material": "AISI 1020"},
        expected_tolerance="ISO 2768-m")}
    for key in ("title", "drawing_number", "revision", "material"):
        assert checks[f"inspect:title_block:{key}"][0] == "passed", checks
    assert checks["inspect:general_tolerance"][0] == "passed"
    wrong = {c.name: c.outcome for c in drawing_dxf_checks(
        SWD / "pocket_title_block.dxf", [], expected_title={"revision": "C"})}
    assert wrong["inspect:title_block:revision"] == "failed"
