"""Workspace Drawing -> 3D: interpretation never builds; a build needs a name, a drawing that proves the
part, and runs once into a fresh folder; only preserved DXF originals that still match their hash."""
import io
import os
import stat
from pathlib import Path

import pytest

from astracad_cad.contract import Op
from astracad_web.server import Workspace

from test_web_text_to_cad import Orch

FIX = Path(__file__).parent / "fixtures"


def make(tmp_path, calls=None):
    return Workspace(tmp_path / "t.db", [tmp_path / "runs"], projects=tmp_path / "projects",
                     orchestrator_factory=(lambda db: Orch(calls)) if calls is not None else None)


def add(w, pid, path: Path, name: str | None = None):
    b = path.read_bytes()
    return w.projects.add_source(pid, name or path.name, io.BytesIO(b), len(b), "Q")


def test_interpret_reads_the_preserved_drawing_and_never_builds(tmp_path):
    calls = []
    w = make(tmp_path, calls)
    pid = w.create_project({"name": "Rebuild"})["id"]
    rec = add(w, pid, FIX / "sw_drawing" / "plate_r5_hlv.dxf")
    d = w.drawing_interpret({"project": pid, "source": rec.sha256})
    assert d["executable"] and d["cad_enabled"] and calls == []
    assert d["expect"]["volume_mm3"] == pytest.approx(58654.424808, abs=1e-6)


def test_build_needs_a_name_and_runs_once_into_a_fresh_folder(tmp_path):
    calls = []
    w = make(tmp_path, calls)
    pid = w.create_project({"name": "Rebuild"})["id"]
    rec = add(w, pid, FIX / "sw_drawing" / "plate_r5_hlv.dxf")
    with pytest.raises(PermissionError):
        w.drawing_build({"project": pid, "source": rec.sha256, "authorize": "  "})
    assert calls == []
    r = w.drawing_build({"project": pid, "source": rec.sha256, "authorize": "K M"})
    (req, auth), = calls
    assert req.operation is Op.CREATE_FEATURE and auth.scope == frozenset({Op.CREATE_FEATURE})
    assert [f["$op"] for f in req.params["spec"]["features"]] == ["sketch.loops", "feature.extrude"]
    assert Path(req.params["out_dir"]).parent == (tmp_path / "runs" / "cad").resolve()
    assert r["authorized_by"] == "K M" and r["status"] == "SUCCEEDED"


def test_only_rebuildable_dxf_originals(tmp_path):
    calls = []
    w = make(tmp_path, calls)
    pid = w.create_project({"name": "Rebuild"})["id"]
    pdf = add(w, pid, FIX / "plate_synthetic.pdf")
    with pytest.raises(ValueError, match="only DXF"):
        w.drawing_interpret({"project": pid, "source": pdf.sha256})
    hid = add(w, pid, FIX / "plate_synthetic.dxf")                    # a drawing that does not prove a part
    with pytest.raises(ValueError, match="not rebuildable"):
        w.drawing_build({"project": pid, "source": hid.sha256, "authorize": "K M"})
    ok = add(w, pid, FIX / "sw_drawing" / "plate_r5_hlv.dxf")
    p = w.projects.source_path(pid, ok.sha256)
    os.chmod(p, stat.S_IWRITE)
    p.write_bytes(p.read_bytes() + b"\n999\nedited outside\n")
    with pytest.raises(ValueError, match="no longer matches"):
        w.drawing_interpret({"project": pid, "source": ok.sha256})
    assert calls == []


def test_options_are_checked(tmp_path):
    w = make(tmp_path, [])
    pid = w.create_project({"name": "Rebuild"})["id"]
    rec = add(w, pid, FIX / "sw_drawing" / "plate_r5_hlv.dxf")
    for bad in ({"scale": "half"}, {"scale": -1}, {"thickness": "thin"}, {"thickness": 0}):
        with pytest.raises(ValueError):
            w.drawing_interpret({"project": pid, "source": rec.sha256, **bad})
