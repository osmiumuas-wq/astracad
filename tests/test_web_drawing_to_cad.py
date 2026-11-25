"""Workspace Drawing -> 3D: interpretation never builds; a build needs a name, a drawing that proves the
part, and runs once into a fresh folder; only preserved DXF originals that still match their hash."""
import io
import json
import os
import shutil
import stat
import subprocess
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


def test_blind_pocket_preview_fallback_uses_its_spec_argument(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed: preview behavior not exercised")
    page = (Path(__file__).parents[1] / "src" / "astracad_web" / "index.html").read_text(encoding="utf-8")
    start = page.index("function loopsPreview(spec) {")
    end = page.index("\nasync function drawingCadDialog", start)
    function = page[start:end]
    script = f"""
const s = (tag, attrs = {{}}, ...children) => ({{tag, attrs, children}});
const h = (tag, attrs = {{}}, ...children) => ({{tag, attrs, children}});
const g6 = (n) => String(n);
const render = new Function('s', 'h', 'g6', {json.dumps(function + "; return loopsPreview;")})(s, h, g6);
const spec = {{features: [{{$op: 'sketch.loops', args: {{loops: [[
  ['L', 0, 0, 100, 0], ['L', 100, 0, 100, 60],
  ['L', 100, 60, 0, 60], ['L', 0, 60, 0, 0]
]]}}}}], notes: {{blind_pockets: [{{x: 50, y: 30, width: 20, height: 10, depth: 5}}]}}}};
process.stdout.write(JSON.stringify(render(spec)));
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    preview = json.loads(result.stdout)
    stack = [preview]
    rects = []
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get("tag") == "rect":
                rects.append(item["attrs"])
            stack.extend(item.get("children") or [])
        elif isinstance(item, list):
            stack.extend(item)
    assert {"x": 40, "y": 25, "width": 20, "height": 10, "class": "pv-pocket"} in rects


def test_options_are_checked(tmp_path):
    w = make(tmp_path, [])
    pid = w.create_project({"name": "Rebuild"})["id"]
    rec = add(w, pid, FIX / "sw_drawing" / "plate_r5_hlv.dxf")
    for bad in ({"scale": "half"}, {"scale": -1}, {"thickness": "thin"}, {"thickness": 0}):
        with pytest.raises(ValueError):
            w.drawing_interpret({"project": pid, "source": rec.sha256, **bad})
