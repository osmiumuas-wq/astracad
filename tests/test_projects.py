"""Projects: creation, upload of untrusted drawings, preservation of the original bytes, and
inspection from the preserved copy (constitution Part 9 items 1-3)."""
import hashlib
import http.client
import io
import json
import os
import stat
import threading
from pathlib import Path

import pytest

from astracad_core.projects import ProjectStore, SourceRejected, safe_filename
from astracad_web.server import Workspace, serve

FIX = Path(__file__).parent / "fixtures"
DXF = (FIX / "plate_synthetic.dxf").read_bytes()
PDF = (FIX / "plate_synthetic.pdf").read_bytes()


def _add(store, pid, name, data, by=""):
    return store.add_source(pid, name, io.BytesIO(data), len(data), by)


# ---- store ---------------------------------------------------------------------------------

def test_source_is_preserved_by_hash_and_read_only(tmp_path):
    s = ProjectStore(tmp_path)
    p = s.create("Bracket line 4")
    rec = _add(s, p.id, "bracket.DXF", DXF, "KM")
    assert rec.sha256 == hashlib.sha256(DXF).hexdigest() and rec.filename == "bracket.dxf"
    f = s.source_path(p.id, rec.sha256)
    assert f.read_bytes() == DXF
    assert not os.access(f, os.W_OK) or not (f.stat().st_mode & stat.S_IWRITE)
    # the same bytes again are recognised, not stored twice
    again = _add(s, p.id, "copy of bracket.dxf", DXF)
    assert again.sha256 == rec.sha256 and again.uploads == 2
    assert len(s.get(p.id).sources) == 1
    assert len(list((tmp_path / p.id / "sources").iterdir())) == 1


@pytest.mark.parametrize("name,data,why", [
    ("part.exe", b"MZ\x90\x00", "supported drawings"),
    ("part.pdf", b"0\nSECTION\n", "not a .pdf"),
    ("part.dxf", b"%PDF-1.7\n", "not a .dxf"),
    ("part.png", b"GIF89a....", "not a .png"),
    ("part", DXF, "no extension"),
], ids=["exe", "pdf-name-dxf-bytes", "dxf-name-pdf-bytes", "png-name-gif-bytes", "no-extension"])
def test_uploads_that_are_not_what_they_claim_are_refused(tmp_path, name, data, why):
    s = ProjectStore(tmp_path)
    p = s.create("Refusals")
    with pytest.raises(SourceRejected, match=why):
        _add(s, p.id, name, data)
    assert s.get(p.id).sources == []
    assert not [x for x in (tmp_path / p.id).iterdir() if x.name.startswith(".upload-")]


def test_truncated_upload_is_refused_and_leaves_nothing(tmp_path):
    s = ProjectStore(tmp_path)
    p = s.create("Truncated")
    with pytest.raises(SourceRejected, match="ended after"):
        s.add_source(p.id, "a.dxf", io.BytesIO(DXF[:100]), len(DXF))
    assert s.get(p.id).sources == []


@pytest.mark.parametrize("raw,clean", [
    ("..\\..\\windows\\system.ini.dxf", "system.ini.dxf"),
    ("../../etc/passwd.pdf", "passwd.pdf"),
    ("CON.dxf", "_CON.dxf"),
    ("a<b>|c?.PDF", "a_b__c_.pdf"),
    ("   ", "drawing"),
])
def test_names_reduce_to_a_plain_file_name(raw, clean):
    assert safe_filename(raw) == clean


def test_project_ids_cannot_escape_the_root(tmp_path):
    s = ProjectStore(tmp_path)
    for bad in ("..", "../x", "a/b", "A_B", "x"):
        with pytest.raises(PermissionError):
            s.get(bad)


def test_modified_preserved_source_is_refused(tmp_path):
    s = ProjectStore(tmp_path)
    p = s.create("Tamper")
    rec = _add(s, p.id, "plate.dxf", DXF)
    f = s.source_path(p.id, rec.sha256)
    os.chmod(f, stat.S_IWRITE | stat.S_IREAD)
    f.write_bytes(DXF + b"999\nedited\n")
    with pytest.raises(ValueError, match="no longer matches"):
        s.source_path(p.id, rec.sha256)


# ---- workspace + HTTP ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def site(tmp_path_factory):
    root = tmp_path_factory.mktemp("proj")
    w = Workspace(root / "t.db", [root / "runs"], projects=root / "projects")
    httpd = serve(w, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield w, httpd.server_address[1]
    httpd.shutdown()


def call(port, method, path, body=None, headers=None, raw=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    h = {"Host": "127.0.0.1", "X-AstraCAD": "1", **(headers or {})}
    c.request(method, path, body=raw if raw is not None else (json.dumps(body) if body is not None else None), headers=h)
    r = c.getresponse()
    data = r.read()
    return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json") else data)


def test_upload_then_inspect_the_preserved_copy(site):
    w, port = site
    st, p = call(port, "POST", "/api/projects", {"name": "Plate family"})
    assert st == 200 and p["id"].startswith("plate-family-")
    st, rec = call(port, "POST", f"/api/upload?project={p['id']}&name=plate.dxf", raw=DXF,
                   headers={"Content-Type": "application/octet-stream"})
    assert st == 201 and rec["sha256"] == hashlib.sha256(DXF).hexdigest()
    st, run = call(port, "POST", "/api/inspect", {"project": p["id"], "source": rec["sha256"]})
    assert st == 200 and run["delivered"]
    # the run lives in the project and records exactly which bytes it read
    assert Path(run["folder"]).parent == w.projects.root / p["id"] / "runs"
    meta = json.loads((Path(run["folder"]) / "plate.run.json").read_text(encoding="utf-8"))
    assert meta["project"] == p["id"] and meta["source_sha256"] == rec["sha256"]
    st, d = call(port, "GET", f"/api/project?id={p['id']}")
    assert st == 200 and len(d["sources"]) == 1 and len(d["runs"]) == 1
    assert d["runs"][0]["project"] == p["id"]
    # the preserved original downloads byte-identical through the same file route
    st, data = call(port, "GET", "/file?key=" + d["sources"][0]["key"])
    assert st == 200 and data == DXF
    st, lst = call(port, "GET", "/api/projects")
    assert any(x["id"] == p["id"] and x["sources"] == 1 and x["runs"] == 1 for x in lst)


def test_upload_refusals_are_answered_not_reset(site):
    w, port = site
    _, p = call(port, "POST", "/api/projects", {"name": "Refusals over HTTP"})
    st, e = call(port, "POST", f"/api/upload?project={p['id']}&name=plate.pdf", raw=DXF)
    assert st == 415 and "not a .pdf" in e["error"]
    st, e = call(port, "POST", f"/api/upload?project={p['id']}&name=tool.exe", raw=b"MZ" * 40000)
    assert st == 415
    st, e = call(port, "POST", f"/api/upload?project={p['id']}&name=plate.dxf", raw=DXF,
                 headers={"X-AstraCAD": ""})
    assert st == 403
    st, e = call(port, "POST", "/api/upload?project=..%2F..&name=plate.dxf", raw=DXF)
    assert st == 403
    st, e = call(port, "POST", "/api/upload?project=no-such-proj&name=plate.dxf", raw=DXF)
    assert st == 404
    assert w.projects.get(p["id"]).sources == []


def test_reissue_of_a_project_run_stays_in_the_project(site):
    w, port = site
    _, p = call(port, "POST", "/api/projects", {"name": "Reissue in project"})
    _, rec = call(port, "POST", f"/api/upload?project={p['id']}&name=plate.pdf", raw=PDF)
    _, run = call(port, "POST", "/api/inspect", {"project": p["id"], "source": rec["sha256"], "units": "mm"})
    st, _ = call(port, "POST", "/api/review", {"key": run["key"], "reviewer": "K M", "action": "approve",
                                               "cid": "CH-001"})
    assert st == 200
    st, new = call(port, "POST", "/api/reissue", {"key": run["key"]})
    assert st == 200 and Path(new["folder"]).parent == w.projects.root / p["id"] / "runs"
    meta = json.loads((Path(new["folder"]) / "plate.run.json").read_text(encoding="utf-8"))
    assert meta["project"] == p["id"] and meta["source_sha256"] == rec["sha256"]
