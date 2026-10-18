"""Signed dossiers: a signature binds a named person's registered key to the exact manifest; every way
of breaking that (another manifest, a removed / reordered / edited signature, a wrong passphrase, a
revoked key) is caught, and a refused signature leaves the package byte-for-byte as it was."""
import base64
import http.client
import io
import json
import threading
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from astracad_inspect import signing
from astracad_inspect.dossier import verify
from astracad_web.server import Workspace, serve

pytestmark = pytest.mark.skipif(not signing.available(), reason="the cryptography package is not installed")

FIX = Path(__file__).parent / "fixtures"
PASS_A, PASS_B = "correct horse battery A", "correct horse battery B"


@pytest.fixture()
def ws(tmp_path):
    w = Workspace(tmp_path / "t.db", [tmp_path / "runs"], keys_dir=tmp_path / "keys")
    r = w.run_inspection(str(FIX / "plate_synthetic.dxf"), None, None)
    w.save_instrument({"by": "Q Lab", "id": "MIC-07", "kind": "micrometer", "unit": "mm", "range_min": 0,
                       "range_max": 150, "resolution": 0.001, "calibration_ref": "C-77",
                       "calibration_due": (date.today() + timedelta(days=90)).isoformat()})
    return w, r["key"]


def dossier_zip(w, key) -> Path:
    return w.resolve_key(w.dossier(key, None)["file"])


def conforming_zip(tmp_path: Path) -> Path:
    """A package whose manifest states conformance - for the approval rules."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, "cover")
    c.save()
    files = {"run/part.plan.json": b'{"rows": []}', "dossier.pdf": buf.getvalue()}
    man = {"generator": "astracad_inspect.dossier", "created_utc": "2026-10-18T10:00:00Z", "plan": "run/part.plan.json",
           "sample": "SN-1", "status": {"conforming": True, "open_items": []},
           "files": [{"path": a, "bytes": len(b), "sha256": signing._sha(b)} for a, b in files.items()]}
    p = tmp_path / "ok_dossier.zip"
    with zipfile.ZipFile(p, "w") as z:
        for a, b in files.items():
            z.writestr(a, b)
        z.writestr("manifest.json", json.dumps(man))
    return p


def rewrite(zip_path: Path, change) -> None:
    with zipfile.ZipFile(zip_path) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    change(entries)
    with zipfile.ZipFile(zip_path, "w") as z:
        for n, b in entries.items():
            z.writestr(n, b)


def test_enrol_keeps_only_an_encrypted_private_key_outside_the_register(ws, tmp_path):
    w, _ = ws
    e = w.signer_action({"action": "enrol", "name": "K  M", "role": "Quality", "passphrase": PASS_A, "confirm": PASS_A})
    assert e["name"] == "K M" and len(e["key_id"]) == 16
    pem = (tmp_path / "keys" / f"{e['key_id']}.pem").read_bytes()
    assert pem.startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    reg = json.loads(w.signers_path.read_text(encoding="utf-8"))
    assert "PRIVATE" not in json.dumps(reg) and reg["signers"][0]["public_key"] == e["public_key"]
    assert not list((tmp_path / "runs").rglob("*.pem"))                    # never in a run folder
    for body, why in (({"passphrase": "short", "confirm": "short"}, "at least 12"),
                      ({"passphrase": PASS_A, "confirm": PASS_A + "x"}, "differ"),
                      ({"passphrase": PASS_B, "confirm": PASS_B}, "already has an active key")):
        with pytest.raises(ValueError, match=why):
            w.signer_action({"action": "enrol", "name": "K M", **body})


def test_sign_binds_the_signer_to_the_exact_manifest(ws):
    w, key = ws
    w.signer_action({"action": "enrol", "name": "K M", "passphrase": PASS_A, "confirm": PASS_A})
    p = dossier_zip(w, key)
    with zipfile.ZipFile(p) as z:
        before = {n: z.read(n) for n in z.namelist()}
    info = w.dossier_sign({"key": w.key_for(p), "name": "K M", "meaning": "prepared", "passphrase": PASS_A})
    (s,) = info["signatures"]
    assert s["valid"] and s["trusted"] and s["signer"] == "K M" and s["meaning"] == "prepared"
    assert all(c["ok"] for c in info["checks"]) and any(c["name"] == "signatures" for c in info["checks"])
    with zipfile.ZipFile(p) as z:
        assert {n: z.read(n) for n in before} == before                     # nothing signed was changed
        rec = json.loads(z.read("signatures/01.json"))
    assert rec["payload"]["manifest_sha256"] == signing._sha(before["manifest.json"])
    # independently: the signature is Ed25519 over the canonical payload, nothing else
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(base64.b64decode(rec["public_key"])).verify(
        base64.b64decode(rec["signature"]),
        json.dumps(rec["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())


def test_a_refused_signature_leaves_the_package_untouched_and_never_echoes_the_passphrase(ws):
    w, key = ws
    w.signer_action({"action": "enrol", "name": "K M", "passphrase": PASS_A, "confirm": PASS_A})
    p = dossier_zip(w, key)
    raw = p.read_bytes()
    wrong = "not-the-passphrase-123"
    with pytest.raises(PermissionError) as ex:
        w.dossier_sign({"key": w.key_for(p), "name": "K M", "meaning": "reviewed", "passphrase": wrong})
    assert wrong not in str(ex.value)
    with pytest.raises(PermissionError, match="no active key"):
        w.dossier_sign({"key": w.key_for(p), "name": "Somebody Else", "meaning": "reviewed", "passphrase": PASS_A})
    with pytest.raises(ValueError, match="not approvable"):                 # this run has open items
        w.dossier_sign({"key": w.key_for(p), "name": "K M", "meaning": "approved", "passphrase": PASS_A})
    assert p.read_bytes() == raw


def test_approval_needs_conformance_and_a_second_person(tmp_path):
    reg, keys = tmp_path / "signers.json", tmp_path / "keys"
    signing.enrol(reg, keys, "Ann Prep", PASS_A)
    signing.enrol(reg, keys, "Bob Appr", PASS_B)
    p = conforming_zip(tmp_path)
    signing.sign(p, reg, keys, "Ann Prep", "prepared", PASS_A, verify)
    with pytest.raises(PermissionError, match="cannot also approve"):
        signing.sign(p, reg, keys, "Ann Prep", "approved", PASS_A, verify)
    with pytest.raises(ValueError, match="already signed"):
        signing.sign(p, reg, keys, "Ann Prep", "prepared", PASS_A, verify)
    sigs = signing.sign(p, reg, keys, "Bob Appr", "approved", PASS_B, verify)
    assert [(s["signer"], s["meaning"], s["valid"], s["trusted"]) for s in sigs] == \
        [("Ann Prep", "prepared", True, True), ("Bob Appr", "approved", True, True)]
    assert all(ok for _, ok, _ in verify(p))


@pytest.mark.parametrize("tamper, problem", [
    (lambda e: e.__setitem__("manifest.json", e["manifest.json"].replace(b"2026-10-18T10:00:00Z", b"2026-10-18T10:00:01Z")),
     "different manifest"),
    (lambda e: e.pop("signatures/01.json"), "chain of earlier signatures"),
    (lambda e: e.__setitem__("signatures/01.json", e["signatures/01.json"].replace(b'"prepared"', b'"reviewed"', 1)),
     "fixed ones"),
    (lambda e: e.__setitem__("signatures/02.json", e["signatures/02.json"].replace(b"Bob Appr", b"Bob Apprx", 1)),
     "does not match its content"),
])
def test_tampering_after_signing_is_caught(tmp_path, tamper, problem):
    reg, keys = tmp_path / "signers.json", tmp_path / "keys"
    signing.enrol(reg, keys, "Ann Prep", PASS_A)
    signing.enrol(reg, keys, "Bob Appr", PASS_B)
    p = conforming_zip(tmp_path)
    signing.sign(p, reg, keys, "Ann Prep", "prepared", PASS_A, verify)
    signing.sign(p, reg, keys, "Bob Appr", "reviewed", PASS_B, verify)
    rewrite(p, tamper)
    sigs = signing.check(p, signing.load_register(reg))
    assert any(problem in pr for s in sigs for pr in s["problems"]), sigs
    assert dict((n, ok) for n, ok, _ in verify(p)).get("signatures") is False
    with pytest.raises(ValueError):                                          # nothing is added on top of it
        signing.sign(p, reg, keys, "Bob Appr", "approved", PASS_B, verify)


@pytest.mark.parametrize("change, problem", [
    (lambda e: e.__setitem__("run/part.plan.json", b'{"rows": [1]}'), "no longer match the signed manifest"),
    (lambda e: e.__setitem__("extra/unsigned.txt", b"added later"), "does not cover were added"),
])
def test_a_signature_never_vouches_for_files_it_did_not_sign(tmp_path, change, problem):
    """Found live 2026-10-18: a dossier with a changed file still showed its signature as trusted,
    because the manifest it signs was intact. The signed statement no longer describes the package."""
    reg, keys = tmp_path / "signers.json", tmp_path / "keys"
    signing.enrol(reg, keys, "Ann Prep", PASS_A)
    p = conforming_zip(tmp_path)
    signing.sign(p, reg, keys, "Ann Prep", "prepared", PASS_A, verify)
    rewrite(p, change)
    (s,) = signing.check(p, signing.load_register(reg))
    assert not s["valid"] and not s["trusted"] and any(problem in pr for pr in s["problems"])
    assert dict((n, ok) for n, ok, _ in verify(p))["signatures"] is False


def test_revoked_key_cannot_sign_and_earlier_signatures_say_so(tmp_path, monkeypatch):
    clock = iter(f"2026-10-18T10:00:{s:02d}Z" for s in range(60))
    monkeypatch.setattr(signing, "_utc", lambda: next(clock))
    reg, keys = tmp_path / "signers.json", tmp_path / "keys"
    e = signing.enrol(reg, keys, "Ann Prep", PASS_A)
    p = conforming_zip(tmp_path)
    signing.sign(p, reg, keys, "Ann Prep", "prepared", PASS_A, verify)
    signing.revoke(reg, e["key_id"], "Q Lead", "left the company")
    (s,) = signing.check(p, signing.load_register(reg))
    assert s["valid"] and s["trusted"] and "revoked later" in s["trust"]
    with pytest.raises(PermissionError, match="no active key"):
        signing.sign(p, reg, keys, "Ann Prep", "reviewed", PASS_A, verify)


def test_an_unregistered_key_checks_out_but_is_not_trusted(tmp_path):
    reg, keys = tmp_path / "signers.json", tmp_path / "keys"
    signing.enrol(reg, keys, "Ann Prep", PASS_A)
    p = conforming_zip(tmp_path)
    signing.sign(p, reg, keys, "Ann Prep", "prepared", PASS_A, verify)
    (s,) = signing.check(p, {"signers": []})
    assert s["valid"] and not s["trusted"] and "not in this register" in s["trust"]


def test_rebuilding_never_overwrites_a_signed_dossier(ws):
    w, key = ws
    w.signer_action({"action": "enrol", "name": "K M", "passphrase": PASS_A, "confirm": PASS_A})
    p = dossier_zip(w, key)
    w.dossier_sign({"key": w.key_for(p), "name": "K M", "meaning": "prepared", "passphrase": PASS_A})
    signed = p.read_bytes()
    again = dossier_zip(w, key)
    assert again != p and again.name.endswith("_v2.zip") and p.read_bytes() == signed
    assert dossier_zip(w, key) == again                                       # unsigned: rebuilt in place


def test_signing_over_http(ws):
    w, key = ws
    p = dossier_zip(w, key)
    httpd = serve(w, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def req(method, path, body=None, header=True):
        c = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=60)
        hd = {"Host": "127.0.0.1", "Content-Type": "application/json"} | ({"X-AstraCAD": "1"} if header else {})
        c.request(method, path, json.dumps(body).encode() if body is not None else None, hd)
        r = c.getresponse()
        return r.status, r.read().decode()
    try:
        st, _ = req("POST", "/api/signers", {"action": "enrol", "name": "K M", "passphrase": PASS_A, "confirm": PASS_A},
                    header=False)
        assert st == 403
        st, _ = req("POST", "/api/signers", {"action": "enrol", "name": "K M", "passphrase": PASS_A, "confirm": PASS_A})
        assert st == 200
        k = w.key_for(p)
        st, txt = req("POST", "/api/dossier_sign", {"key": k, "name": "K M", "meaning": "prepared", "passphrase": "wrong-wrong-wrong"})
        assert st == 403 and "wrong-wrong-wrong" not in txt
        st, txt = req("POST", "/api/dossier_sign", {"key": k, "name": "K M", "meaning": "prepared", "passphrase": PASS_A})
        assert st == 200 and json.loads(txt)["signatures"][0]["trusted"]
        st, txt = req("GET", "/api/dossier_info?key=" + k.replace("/", "%2F"))
        assert st == 200 and len(json.loads(txt)["signatures"]) == 1
        plan_key = key.replace("/", "%2F")
        st, _ = req("GET", "/api/dossier_info?key=" + plan_key)                 # a plan is not a dossier
        assert st == 403
    finally:
        httpd.shutdown()


def test_command_line_enrol_sign_and_verify(tmp_path, monkeypatch, capsys):
    from astracad_inspect import cli
    answers = iter([PASS_A, PASS_A, "wrong-passphrase-here", PASS_A])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    reg, keys = tmp_path / "signers.json", tmp_path / "keys"
    common = ["--register", str(reg), "--keys", str(keys)]
    assert cli.main(["dossier", "enrol", "--signer", "Ann Prep", *common]) == 0
    p = conforming_zip(tmp_path)
    assert cli.main(["dossier", "sign", str(p), "--signer", "Ann Prep", "--meaning", "prepared", *common]) == 1
    assert "passphrase does not open" in capsys.readouterr().err
    assert cli.main(["dossier", "sign", str(p), "--signer", "Ann Prep", "--meaning", "prepared", *common]) == 0
    assert cli.main(["dossier", "verify", str(p), "--register", str(reg)]) == 0
    assert "TRUSTED prepared  Ann Prep" in capsys.readouterr().out
    rewrite(p, lambda e: e.__setitem__("run/part.plan.json", b"{}"))
    assert cli.main(["dossier", "verify", str(p), "--register", str(reg)]) == 3
