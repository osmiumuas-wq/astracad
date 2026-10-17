"""Quality dossier: the package holds the run's records with verified hashes, includes the source
drawing only when it still matches, and states conformance only when nothing is open."""
import json
import threading
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from astracad_web.server import Workspace, serve

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture()
def run(tmp_path):
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"])
    r = w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    plan = json.loads(w.resolve_key(r["key"]).read_text(encoding="utf-8"))
    w.save_instrument({"by": "Q Lab", "id": "MIC-07", "kind": "micrometer", "unit": "mm", "range_min": 0, "range_max": 150,
                       "resolution": 0.001, "calibration_ref": "C-77", "calibration_due": (date.today() + timedelta(days=90)).isoformat()})
    return w, r["key"], plan


def test_dossier_contents_hashes_and_open_items(run):
    w, key, plan = run
    cid = next(x["cid"] for x in plan["rows"] if x["callout"] == "100 ±0.1")
    w.measure(key, {"inspector": "K M", "sample": "SN-1", "cid": cid, "value": 100.2, "instrument": "MIC-07"})
    n = w.ncr_action({"action": "create", "key": key, "cid": cid, "sample": "SN-1", "by": "K M", "description": "over limit"})
    w.checklist_decide({"key": key, "template": "drawing_review", "by": "Q", "item": "units", "result": "PASS"})
    out = w.dossier(key, "SN-1")
    assert all(c["ok"] for c in out["checks"]), out["checks"]
    assert not out["status"]["conforming"]
    items = " | ".join(out["status"]["open_items"])
    assert "plan not releasable" in items and n["number"] in items and "not signed" in items and "failed" in items
    assert "included (SHA-256 verified)" in out["drawing"]
    with zipfile.ZipFile(w.resolve_key(out["file"])) as z:
        names = z.namelist()
        man = json.loads(z.read("manifest.json"))
    assert "dossier.pdf" in names and f"ncr/{n['number']}.json" in names and "source/plate_synthetic.dxf" in names
    assert any(x.endswith("_FAI_SN-1.xlsx") for x in names) and any("checklist_drawing_review" in x for x in names)
    assert any(x.endswith(".measurements.jsonl") for x in names)
    assert man["sample"] == "SN-1" and len(man["files"]) == len(names) - 1


def test_changed_source_drawing_is_left_out_and_said_so(run, tmp_path):
    w, key, plan = run
    meta_p = w.resolve_key(key).with_name("plate_synthetic.run.json")
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    fake = tmp_path / "plate_synthetic.dxf"
    fake.write_bytes(Path(meta["drawing"]).read_bytes() + b"999\nchanged\n")
    meta["drawing"] = str(fake)
    meta_p.write_text(json.dumps(meta), encoding="utf-8")
    out = w.dossier(key, None)
    assert "NOT included" in out["drawing"] and all(c["ok"] for c in out["checks"])
    with zipfile.ZipFile(w.resolve_key(out["file"])) as z:
        assert not any(n.startswith("source/") for n in z.namelist())


def test_tampered_package_fails_verification(run):
    from astracad_inspect.dossier import verify
    w, key, plan = run
    out = w.dossier(key, None)
    p = w.resolve_key(out["file"])
    with zipfile.ZipFile(p) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    victim = next(n for n in entries if n.endswith(".plan.json"))
    entries[victim] = entries[victim].replace(b"plate", b"PLATE", 1)
    with zipfile.ZipFile(p, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)
    assert dict((n, ok) for n, ok, _ in verify(p))["manifest_hashes"] is False


def test_unknown_serial_is_refused(run):
    w, key, plan = run
    with pytest.raises(ValueError, match="no measurements"):
        w.dossier(key, "SN-404")


# ---- project-level dossier tests ---------------------------------------------------------


@pytest.fixture()
def project_run(tmp_path):
    import io
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"], projects=tmp_path / "projects")
    p = w.create_project({"name": "Test Bracket Project", "description": "Project quality dossier test"})
    dxf_bytes = (FIX / "plate_synthetic.dxf").read_bytes()
    rec = w.projects.add_source(p["id"], "plate_synthetic.dxf", io.BytesIO(dxf_bytes), len(dxf_bytes), "KM")
    insp = w.inspect_source({"project": p["id"], "source": rec.sha256})
    plan = json.loads(w.resolve_key(insp["key"]).read_text(encoding="utf-8"))
    w.save_instrument({"by": "Q Lab", "id": "MIC-07", "kind": "micrometer", "unit": "mm", "range_min": 0, "range_max": 150,
                       "resolution": 0.001, "calibration_ref": "C-77", "calibration_due": (date.today() + timedelta(days=90)).isoformat()})
    return w, p["id"], insp["key"], plan


def test_project_dossier_contents_hashes_and_open_items(project_run):
    w, pid, key, plan = project_run
    cid = next(x["cid"] for x in plan["rows"] if x["callout"] == "100 ±0.1")
    w.measure(key, {"inspector": "K M", "sample": "SN-1", "cid": cid, "value": 100.2, "instrument": "MIC-07"})
    n = w.ncr_action({"action": "create", "key": key, "cid": cid, "sample": "SN-1", "by": "K M", "description": "over limit"})
    w.checklist_decide({"key": key, "template": "drawing_review", "by": "Q", "item": "units", "result": "PASS"})
    out = w.project_dossier(pid)
    assert all(c["ok"] for c in out["checks"]), out["checks"]
    assert not out["status"]["conforming"]
    items = " | ".join(out["status"]["open_items"])
    assert "plan not releasable" in items and n["number"] in items and "not signed" in items and "failed" in items
    assert out["runs"] == 1
    assert any("plate_synthetic.dxf: included (SHA-256 verified)" in s for s in out["sources"])
    with zipfile.ZipFile(w.resolve_key(out["file"])) as z:
        names = z.namelist()
        man = json.loads(z.read("manifest.json"))
    assert "dossier.pdf" in names and f"ncr/{n['number']}.json" in names and "sources/plate_synthetic.dxf" in names
    assert any(x.startswith("runs/") and x.endswith(".plan.json") for x in names)
    assert any(x.startswith("runs/") and x.endswith("_FAI_SN-1.xlsx") for x in names)
    assert any(x.startswith("runs/") and "checklist_drawing_review" in x for x in names)
    assert man["type"] == "project" and man["project"] == "Test Bracket Project"
    assert len(man["files"]) == len(names) - 1


def test_project_dossier_empty_project_refused(tmp_path):
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"], projects=tmp_path / "projects")
    p = w.create_project({"name": "Empty Project"})
    with pytest.raises(ValueError, match="this project has no inspection runs"):
        w.project_dossier(p["id"])


def test_project_dossier_tampered_package_fails_verification(project_run):
    from astracad_inspect.dossier import verify
    w, pid, key, plan = project_run
    out = w.project_dossier(pid)
    p = w.resolve_key(out["file"])
    with zipfile.ZipFile(p) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    victim = next(n for n in entries if n.endswith(".plan.json"))
    entries[victim] = entries[victim].replace(b"plate", b"PLATE", 1)
    with zipfile.ZipFile(p, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)
    assert dict((n, ok) for n, ok, _ in verify(p))["manifest_hashes"] is False


def test_project_dossier_http_api(project_run):
    import http.client
    w, pid, key, plan = project_run
    srv = serve(w, "127.0.0.1", 0)
    port = srv.server_port
    t = threading.Thread(target=srv.serve_forever)
    t.daemon = True
    t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        body = json.dumps({"project": pid}).encode("utf-8")
        conn.request("POST", "/api/project_dossier", body,
                     headers={"Content-Type": "application/json", "X-AstraCAD": "1"})
        res = conn.getresponse()
        assert res.status == 200
        data = json.loads(res.read().decode("utf-8"))
        assert data["runs"] == 1
        assert "file" in data and "checks" in data
        assert all(c["ok"] for c in data["checks"])
    finally:
        srv.shutdown()


def test_project_status_conforming_and_nonconforming():
    from astracad_inspect.dossier import project_status
    # 1. Clean run: approvable, sample acceptable, no open NCRs, checklist signed
    clean_run = {
        "stem": "run_01",
        "plan": {"approvable": True, "gate": "releasable", "rows": []},
        "meas": {"sample_state": {"SN-1": {"state": "ACCEPTABLE", "fails": [], "missing": [], "not_evaluable": []}}},
        "ncrs": [{"number": "NCR-2026-0001", "status": "CLOSED"}],
        "checklists": [{"title": "Drawing Review", "signed": {"by": "KM"}, "decided": 5, "total": 5}],
        "samples": ["SN-1"],
    }
    st = project_status([clean_run])
    assert st["conforming"] is True
    assert st["open_items"] == []
    assert len(st["run_statuses"]) == 1

    # 2. Nonconforming run: open NCR and unsigned checklist
    dirty_run = {
        "stem": "run_02",
        "plan": {"approvable": False, "gate": "NOT releasable - missing items", "rows": []},
        "meas": {"sample_state": {"SN-2": {"state": "FAILED", "fails": ["CH-001"], "missing": [], "not_evaluable": []}}},
        "ncrs": [{"number": "NCR-2026-0002", "status": "OPEN"}],
        "checklists": [{"title": "Setup", "signed": None, "decided": 2, "total": 5}],
        "samples": ["SN-2"],
    }
    st2 = project_status([clean_run, dirty_run])
    assert st2["conforming"] is False
    assert len(st2["open_items"]) >= 3
    assert any("NCR-2026-0002 is open" in x for x in st2["open_items"])
    assert any("checklist 'Setup' not signed" in x for x in st2["open_items"])


def test_project_dossier_tampered_source_is_omitted_and_noted(project_run):
    import os
    import stat
    w, pid, key, plan = project_run
    # Modify the preserved source file directly on disk
    src_rec = w.projects.get(pid).sources[0]
    p = w.projects.root / pid / "sources" / src_rec.sha256 / src_rec.filename
    os.chmod(p, stat.S_IWRITE)
    p.write_bytes(p.read_bytes() + b"\ncorrupted\n")

    out = w.project_dossier(pid)
    assert any("NOT included" in s for s in out["sources"])
    with zipfile.ZipFile(w.resolve_key(out["file"])) as z:
        assert not any(n.startswith("sources/") for n in z.namelist())


