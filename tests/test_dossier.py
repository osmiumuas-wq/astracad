"""Quality dossier: the package holds the run's records with verified hashes, includes the source
drawing only when it still matches, and states conformance only when nothing is open."""
import json
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from astracad_web.server import Workspace

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
