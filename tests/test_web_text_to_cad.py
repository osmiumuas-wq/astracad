"""Workspace Text -> CAD: interpret never executes; build needs a name, a complete request, and
runs once into a fresh folder under exactly that authorisation."""
import threading
from pathlib import Path

import pytest

from astracad_cad.contract import Op
from astracad_web.server import Workspace

PLATE = ("SolidWorks 100 x 40 x 10 mm plate with 4 x 6 mm holes at 20 mm pitch along the width, "
         "centred, export STEP")


class Result:
    def __init__(self, out):
        self.out = out

    def to_dict(self):
        p = Path(self.out) / "part.step"
        p.write_text("ISO-10303-21;")
        return {"status": "SUCCEEDED", "evidence": "END_TO_END_VERIFIED",
                "checks": [{"name": "kernel:volume_mm3", "outcome": "passed"}],
                "errors": [], "artifacts": [{"path": str(p)}]}


class Orch:
    def __init__(self, calls, gate=None):
        self.calls, self.gate = calls, gate

    def submit(self, req, auth):
        self.calls.append((req, auth))
        if self.gate:
            self.gate.wait(5)
        return "T-1", Result(req.params["out_dir"])


def make(tmp_path, calls, gate=None):
    return Workspace(tmp_path / "t.db", [tmp_path / "runs"],
                     orchestrator_factory=lambda db: Orch(calls, gate))


def test_interpret_never_submits(tmp_path):
    calls = []
    d = make(tmp_path, calls).cad_interpret(PLATE)
    assert d["executable"] and d["cad_enabled"] and calls == []
    with pytest.raises(ValueError):
        make(tmp_path, calls).cad_interpret("   ")


def test_build_needs_a_name_and_a_complete_request(tmp_path):
    calls = []
    w = make(tmp_path, calls)
    with pytest.raises(PermissionError):
        w.cad_build(PLATE, "  ")
    with pytest.raises(ValueError, match="not executable"):
        w.cad_build("SolidWorks 100 x 40 x 10 mm plate with 4 x 6 mm holes at 20 mm pitch", "KM")
    with pytest.raises(ValueError):
        w.cad_build('change D1@Boss-Extrude1 of "C:\\p\\a.SLDPRT" to 5 mm', "KM")
    assert calls == []


def test_build_is_scoped_to_one_new_folder(tmp_path):
    calls = []
    r = make(tmp_path, calls).cad_build(PLATE, "  K  M ")
    (req, auth), = calls
    assert req.operation is Op.CREATE_FEATURE and auth.scope == frozenset({Op.CREATE_FEATURE})
    assert auth.granted_by == "K M" and r["authorized_by"] == "K M"
    out = Path(req.params["out_dir"])
    assert auth.allowed_output_dirs == (str(out),) and out.parent == (tmp_path / "runs" / "cad").resolve()
    assert req.params["expect"]["volume_mm3"] == pytest.approx(38869.02664470769)
    assert r["status"] == "SUCCEEDED" and r["files"][0]["key"].endswith("/part.step")


def test_cad_off_refuses_build(tmp_path):
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"], orchestrator_factory=None)
    assert w.cad_interpret(PLATE)["cad_enabled"] is False
    with pytest.raises(PermissionError, match="switched off"):
        w.cad_build(PLATE, "KM")


def test_second_build_while_one_runs_is_refused(tmp_path):
    calls, gate = [], threading.Event()
    w = make(tmp_path, calls, gate)
    t = threading.Thread(target=w.cad_build, args=(PLATE, "KM"))
    t.start()
    try:
        for _ in range(100):
            if calls:
                break
            threading.Event().wait(0.02)
        with pytest.raises(PermissionError, match="another CAD build"):
            w.cad_build(PLATE, "KM")
    finally:
        gate.set()
        t.join()
    assert len(calls) == 1
