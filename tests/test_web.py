"""Local web workspace: what it reads, the one action it allows, and the local-only hardening."""
import http.client
import json
import shutil
import threading
from pathlib import Path

import pytest

from astracad_core.tasks import TaskStore
from astracad_web.server import Workspace, serve

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    root = tmp_path_factory.mktemp("web")
    db = root / "t.db"
    TaskStore(db).create("build a 60 x 40 x 20 mm block")
    runs = root / "runs"
    w = Workspace(db, [runs], orchestrator_factory=None)
    w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    w.run_inspection(str(FIX / "plate_synthetic.pdf"), "mm", "B")
    (runs / "secret.txt").write_text("x")
    return w


@pytest.fixture(scope="module")
def server(ws):
    httpd = serve(ws, "127.0.0.1", 0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    c.request(method, path, body=json.dumps(body) if body is not None else None,
              headers=headers or {})
    r = c.getresponse()
    data = r.read()
    return r.status, (json.loads(data) if r.getheader("Content-Type", "").startswith("application/json")
                      else data)


def test_inspections_list_both_routes(ws):
    runs = ws.inspections()
    assert {r["route"] for r in runs} == {"dxf-vector", "pdf-vector"}
    pdf = next(r for r in runs if r["route"] == "pdf-vector")
    assert pdf["rows"] == 9 and pdf["needs_review"] == 9 and pdf["revision"] == "B"
    assert pdf["exports_passed"] == pdf["exports_total"] > 0 and pdf["approvable"] is False


def test_inspection_detail_links_its_files(ws):
    key = ws.inspections()[0]["key"]
    d = ws.inspection(key)
    assert set(d["files"]) == {"layout", "sheet", "report", "workbook"} and d["plan"]["rows"]


def test_tasks_are_read_from_the_shared_store(ws):
    assert ws.tasks()[0]["request"] == "build a 60 x 40 x 20 mm block"


def test_keys_cannot_escape_the_run_folder(ws):
    for bad in ("0/../t.db", "0/..\t.db", "9/x", "0/", "x/y", "0/nope.png"):
        with pytest.raises(PermissionError):
            ws.resolve_key(bad)


def test_unsupported_drawing_type_is_refused(ws, tmp_path):
    p = tmp_path / "a.dwg"
    p.write_bytes(b"x")
    with pytest.raises(ValueError):
        ws.run_inspection(str(p), "mm", None)


def test_http_pages_and_files(server, ws):
    status, page = req(server, "GET", "/", headers={"Host": "127.0.0.1"})
    assert status == 200 and b"ASTraCAD" in page
    status, runs = req(server, "GET", "/api/inspections", headers={"Host": "localhost:1"})
    assert status == 200 and len(runs) == 2
    layout = ws.inspection(runs[0]["key"])["files"]["layout"]
    status, png = req(server, "GET", "/file?key=" + layout, headers={"Host": "127.0.0.1"})
    assert status == 200 and png[:4] == b"\x89PNG"


def test_http_refuses_foreign_host_header(server):
    status, body = req(server, "GET", "/api/tasks", headers={"Host": "evil.example"})
    assert status == 403


def test_http_refuses_traversal(server):
    status, _ = req(server, "GET", "/file?key=0/..%2Ft.db", headers={"Host": "127.0.0.1"})
    assert status == 403


def test_post_needs_the_custom_header(server):
    body = {"drawing": str(FIX / "plate_synthetic.dxf")}
    status, d = req(server, "POST", "/api/inspect", body, {"Host": "127.0.0.1",
                                                           "Content-Type": "application/json"})
    assert status == 403 and "X-AstraCAD" in d["error"]
    status, d = req(server, "POST", "/api/inspect", {"drawing": "C:/no/such.dxf"},
                    {"Host": "127.0.0.1", "X-AstraCAD": "1"})
    assert status == 400


def test_same_stem_runs_in_one_second_never_share_a_folder(tmp_path):
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"])
    a = w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    b = w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    assert a["folder"] != b["folder"] and len(w.inspections()) == 2


def test_oversized_post_body_is_refused(server):
    status, d = req(server, "POST", "/api/inspect", {"drawing": "x" * 70000},
                    {"Host": "127.0.0.1", "X-AstraCAD": "1"})
    assert status == 413


def test_interactive_sheet_maps_drawing_coordinates_to_pixels(ws):
    """The viewer draws balloons itself from plan.layout over the sheet image. That is only right if
    the image's own frame is exactly the recorded extents, so check a dark pixel lands on geometry."""
    import numpy as np
    from PIL import Image
    key = next(r["key"] for r in ws.inspections() if r["route"] == "dxf-vector")
    d = ws.inspection(key)
    sh = d["sheet"]
    x0, y0, x1, y1 = sh["extents"]
    img = Image.open(ws.resolve_key(d["files"]["sheet"])).convert("L")
    assert img.size == (sh["width_px"], sh["height_px"])
    # aspect of pixels equals aspect of drawing units: no stretch
    assert abs(img.size[0] / img.size[1] - (x1 - x0) / (y1 - y0)) < 0.01
    a = np.asarray(img)
    px = lambda x, y: (int((x - x0) / (x1 - x0) * (img.size[0] - 1)), int((y1 - y) / (y1 - y0) * (img.size[1] - 1)))
    # the plate's bottom edge runs along y = 0 from x = 10 to 90: ink within 2 px of the mapped line
    for x in (30.0, 50.0, 70.0):
        cx, cy = px(x, 0.0)
        assert a[max(0, cy - 2):cy + 3, cx].min() < 128, (x, cx, cy)
    # and the centre of the large hole (50, 25) is paper, not ink
    cx, cy = px(50.0, 25.0)
    assert a[cy, cx] > 200


def test_inspection_carries_per_characteristic_evidence(ws):
    key = next(r["key"] for r in ws.inspections() if r["route"] == "dxf-vector")
    d = ws.inspection(key)
    assert set(d["evidence"]) == {r["cid"] for r in d["plan"]["rows"]}
    e = d["evidence"][d["plan"]["rows"][0]["cid"]]
    assert e["history"] and {"anchor_x", "anchor_y", "entity_handle"} <= set(e["source"])
    assert d["plan"]["layout"]["balloons"]


def test_page_script_is_valid_javascript(tmp_path):
    """A syntax error blanks the whole workspace; check the inline script parses (needs Node)."""
    import shutil as _sh
    import subprocess
    node = _sh.which("node")
    if not node:
        pytest.skip("node not installed: page script not syntax-checked")
    s = (Path(__file__).parents[1] / "src" / "astracad_web" / "index.html").read_text(encoding="utf-8")
    js = tmp_path / "page.js"
    js.write_text(s[s.index("<script>") + 8:s.rindex("</script>")], encoding="utf-8")
    r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
