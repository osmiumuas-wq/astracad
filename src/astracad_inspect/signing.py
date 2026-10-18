"""Signed quality dossiers: a named person's Ed25519 signature over a dossier's manifest.

What a signature says - one of three fixed statements, about exactly the files the manifest lists
(each by SHA-256), made by whoever holds a registered key and typed its passphrase:

- prepared - "I prepared this dossier; it holds the records its manifest lists."
- reviewed - "I reviewed the records in this dossier."
- approved - "I approve these records; they show conformance with no open item." Only when the
  dossier states conformance, and never by a person who signed it as prepared (two-person rule).

It says nothing more: not that every file was read, not *when* beyond this PC's clock (there is no
trusted timestamp), and not who the signer is beyond the register entry that names the key.

Format, so a recipient can check it with any Ed25519 library (see docs/engineering/dossier-signatures.md):
`signatures/NN.json` in the ZIP holds {"payload": {...}, "algorithm": "Ed25519", "public_key":
base64(32 raw bytes), "signature": base64(64 bytes)}. The signed bytes are
`json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.
`payload.manifest_sha256` is the SHA-256 of manifest.json as stored in the ZIP, and the manifest
lists every other file's SHA-256, so the signature covers the whole package. `payload.previous`
lists the SHA-256 of each earlier signature file, so none can be removed or reordered unnoticed.

Keys are made here. The private key is kept only encrypted with the signer's passphrase (PKCS#8,
the library's best available encryption) in a key folder outside every project and run folder; the
register (`signers.json`, public keys only) sits with the instrument register. The passphrase is
never stored or logged.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

FORMAT = "astracad-dossier-signature/1"
MEANINGS = {
    "prepared": "I prepared this dossier; it holds the records its manifest lists.",
    "reviewed": "I reviewed the records in this dossier.",
    "approved": "I approve these records; they show conformance with no open item.",
}
MIN_PASSPHRASE = 12
_SIG_FILE = re.compile(r"signatures/(\d{2})\.json")
_NAME = re.compile(r"[^\x00-\x1f\x7f]{2,80}")


def available() -> bool:
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: F401
        return True
    except ImportError:
        return False


def _need_crypto():
    if not available():
        raise RuntimeError("signing needs the 'cryptography' package (pip install cryptography)")
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    return ed25519, serialization, InvalidSignature


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def key_id(public_raw: bytes) -> str:
    """First 16 hex digits of the SHA-256 of the raw public key: what a signer reads out to a recipient."""
    return _sha(public_raw)[:16]


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip()


# ---- register (public keys only) ------------------------------------------------------------------
def load_register(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"signers": [], "log": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise ValueError(f"{p.name} could not be read ({type(ex).__name__}); it was left unchanged") from ex
    if not isinstance(d, dict) or not isinstance(d.get("signers"), list):
        raise ValueError(f"{p.name} is not a signer register; it was left unchanged")
    d.setdefault("log", [])
    return d


def _save_register(path: Path, reg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(reg, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def active_key(reg: dict, name: str) -> dict | None:
    n = _norm_name(name).casefold()
    hits = [s for s in reg["signers"] if s["name"].casefold() == n and not s.get("revoked_utc")]
    return hits[0] if hits else None


def enrol(register_path: str | Path, keys_dir: str | Path, name: str, passphrase: str, role: str = "") -> dict:
    """Make a key pair for a named person. Only the encrypted private key is written (once, never
    overwritten); the register gets the public key. One active key per name."""
    ed25519, serialization, _ = _need_crypto()
    name = _norm_name(name)
    if not _NAME.fullmatch(name):
        raise ValueError("signer name: 2 to 80 printable characters")
    if len(passphrase or "") < MIN_PASSPHRASE:
        raise ValueError(f"the passphrase must have at least {MIN_PASSPHRASE} characters")
    if _norm_name(passphrase).casefold() == name.casefold():
        raise ValueError("the passphrase must not be the name")
    register_path, keys_dir = Path(register_path), Path(keys_dir)
    reg = load_register(register_path)
    if active_key(reg, name):
        raise ValueError(f"{name} already has an active key; revoke it before making a new one")
    key = ed25519.Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    kid = key_id(pub)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.BestAvailableEncryption(passphrase.encode("utf-8")))
    keys_dir.mkdir(parents=True, exist_ok=True)
    with open(keys_dir / f"{kid}.pem", "xb") as f:          # never overwrites a key file
        f.write(pem)
    entry = {"key_id": kid, "name": name, "role": str(role or "").strip()[:80],
             "public_key": base64.b64encode(pub).decode("ascii"), "algorithm": "Ed25519",
             "enrolled_utc": _utc(), "revoked_utc": None, "revoked_by": None, "revoked_reason": None}
    reg["signers"].append(entry)
    reg["log"].append({"at": entry["enrolled_utc"], "action": "enrol", "key_id": kid, "name": name})
    _save_register(register_path, reg)
    return entry


def revoke(register_path: str | Path, kid: str, by: str, reason: str) -> dict:
    """Mark a key revoked (a lost passphrase, a person leaving, a key that may be known to others).
    Signatures made before stay checkable and say the key was revoked later."""
    by, reason = _norm_name(by), str(reason or "").strip()
    if not _NAME.fullmatch(by):
        raise ValueError("the person revoking must be named")
    if not reason:
        raise ValueError("a reason is required")
    register_path = Path(register_path)
    reg = load_register(register_path)
    entry = next((s for s in reg["signers"] if s["key_id"] == kid), None)
    if entry is None:
        raise FileNotFoundError(f"no key {kid!r} in the register")
    if entry.get("revoked_utc"):
        raise ValueError(f"key {kid} was already revoked on {entry['revoked_utc']}")
    entry.update(revoked_utc=_utc(), revoked_by=by, revoked_reason=reason[:300])
    reg["log"].append({"at": entry["revoked_utc"], "action": "revoke", "key_id": kid, "by": by, "reason": reason[:300]})
    _save_register(register_path, reg)
    return entry


# ---- signatures in a dossier ZIP -------------------------------------------------------------------
def signature_files(z: zipfile.ZipFile) -> list[str]:
    return sorted(n for n in z.namelist() if _SIG_FILE.fullmatch(n))


def is_signed(zip_path: str | Path) -> bool:
    p = Path(zip_path)
    if not p.is_file():
        return False
    with zipfile.ZipFile(p) as z:
        return bool(signature_files(z))


def check(zip_path: str | Path, register: dict | None = None) -> list[dict]:
    """Every signature in the package, in order. `valid`: the signature is genuine for its public key,
    names this manifest and keeps the chain of earlier signatures. `trusted`: in addition the key is
    in `register` under the same name and was not revoked when it signed. Nothing is skipped."""
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        man_raw = z.read("manifest.json") if "manifest.json" in names else b"{}"
        man_sha = _sha(man_raw) if "manifest.json" in names else None
        files = [(n, z.read(n)) for n in signature_files(z)]
        # A signature vouches for the manifest; the manifest vouches for the files. When a file no
        # longer matches it, or files it does not list were added, the signed statement does not
        # describe this package - so no signature on it is reported as checking out.
        try:
            listed = json.loads(man_raw).get("files") or []
        except ValueError:
            listed = []
        changed = [f["path"] for f in listed if f["path"] not in names or _sha(z.read(f["path"])) != f["sha256"]]
        added = sorted(names - {f["path"] for f in listed} - {"manifest.json"} - {n for n, _ in files})
    package_problems = []
    if changed:
        package_problems.append(f"the package's files no longer match the signed manifest ({changed[0]}"
                                + (f" and {len(changed) - 1} more)" if len(changed) > 1 else ")"))
    if added:
        package_problems.append(f"files the signature does not cover were added ({added[0]}"
                                + (f" and {len(added) - 1} more)" if len(added) > 1 else ")"))
    by_kid = {s["key_id"]: s for s in (register or {}).get("signers", [])}
    out, prev = [], []
    for i, (name, raw) in enumerate(files, start=1):
        r: dict[str, Any] = {"file": name, "valid": False, "trusted": False, "problems": [], "trust": ""}
        try:
            d = json.loads(raw)
            p = d["payload"]
            r.update(signer=p["signer"]["name"], key_id=p["signer"]["key_id"], meaning=p["meaning"],
                     signed_utc=p["signed_utc"], statement=p.get("statement"))
        except (ValueError, KeyError, TypeError) as ex:
            r["problems"].append(f"unreadable signature file ({type(ex).__name__})")
            out.append(r)
            prev.append(_sha(raw))
            continue
        if name != f"signatures/{i:02d}.json":
            r["problems"].append("signature files are not numbered in sequence")
        if d.get("algorithm") != "Ed25519" or p.get("format") != FORMAT:
            r["problems"].append(f"unknown format {p.get('format')!r} / {d.get('algorithm')!r}")
        if p.get("meaning") not in MEANINGS or p.get("statement") != MEANINGS.get(p.get("meaning")):
            r["problems"].append("meaning or statement is not one of the fixed ones")
        if p.get("manifest_sha256") != man_sha:
            r["problems"].append("signed a different manifest than the one in this package")
        r["problems"] += package_problems
        if p.get("previous") != prev:
            r["problems"].append("the chain of earlier signatures does not match (one removed, changed or reordered)")
        try:
            pub = base64.b64decode(d["public_key"], validate=True)
            sig = base64.b64decode(d["signature"], validate=True)
            if key_id(pub) != p["signer"]["key_id"]:
                r["problems"].append("the key id does not belong to the public key")
            if available():
                ed25519, _, InvalidSignature = _need_crypto()
                try:
                    ed25519.Ed25519PublicKey.from_public_bytes(pub).verify(sig, canonical(p))
                except InvalidSignature:
                    r["problems"].append("the signature does not match its content")
            else:
                r["problems"].append("cannot check: the 'cryptography' package is not installed")
        except (ValueError, KeyError, TypeError) as ex:
            r["problems"].append(f"bad key or signature encoding ({type(ex).__name__})")
        r["valid"] = not r["problems"]
        entry = by_kid.get(r.get("key_id", ""))
        if register is None:
            r["trust"] = "no register given: compare the key id with the signer"
        elif entry is None:
            r["trust"] = "key not in this register: compare the key id with the signer"
        elif _norm_name(entry["name"]).casefold() != _norm_name(r["signer"]).casefold():
            r["trust"] = f"the register names this key {entry['name']!r}"
        elif entry.get("revoked_utc") and entry["revoked_utc"] <= r["signed_utc"]:
            r["trust"] = f"signed after the key was revoked ({entry['revoked_utc']})"
        else:
            r["trusted"] = r["valid"]
            r["trust"] = "registered key" + (f"; revoked later ({entry['revoked_utc']})" if entry.get("revoked_utc") else "")
        out.append(r)
        prev.append(_sha(raw))
    return out


def sign(zip_path: str | Path, register_path: str | Path, keys_dir: str | Path, name: str, meaning: str,
         passphrase: str, package_checks: Callable[[Path], list[tuple[str, bool, str]]]) -> list[dict]:
    """Add one signature. Refused - and the package left byte-for-byte as it was - when the package
    fails its own checks, an earlier signature does not check out, the passphrase does not open the
    signer's registered key, or the meaning's rule is not met."""
    ed25519, serialization, _ = _need_crypto()
    zip_path, register_path, keys_dir = Path(zip_path), Path(register_path), Path(keys_dir)
    name, meaning = _norm_name(name), str(meaning or "").strip().lower()
    if meaning not in MEANINGS:
        raise ValueError(f"meaning must be one of {', '.join(MEANINGS)}")
    bad = [f"{n}: {d}" for n, ok, d in package_checks(zip_path) if not ok]
    if bad:
        raise ValueError("the package fails its own checks, so it is not signed: " + "; ".join(bad))
    reg = load_register(register_path)
    entry = active_key(reg, name)
    if entry is None:
        raise PermissionError(f"{name or 'this signer'} has no active key in the register; enrol first")
    key_file = keys_dir / f"{entry['key_id']}.pem"
    if not key_file.is_file():
        raise PermissionError(f"the private key for {name} ({entry['key_id']}) is not on this PC")
    try:
        key = serialization.load_pem_private_key(key_file.read_bytes(), password=(passphrase or "").encode("utf-8"))
    except (ValueError, TypeError):
        raise PermissionError(f"the passphrase does not open {name}'s key") from None
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if not isinstance(key, ed25519.Ed25519PrivateKey) or base64.b64encode(pub).decode("ascii") != entry["public_key"]:
        raise PermissionError("the key file does not match the register entry")
    earlier = check(zip_path, reg)
    if any(not s["valid"] for s in earlier):
        raise ValueError("an earlier signature does not check out, so no signature is added: "
                         + "; ".join(p for s in earlier for p in s["problems"]))
    if any(s["meaning"] == meaning and _norm_name(s["signer"]).casefold() == name.casefold() for s in earlier):
        raise ValueError(f"{name} has already signed this dossier as {meaning}")
    with zipfile.ZipFile(zip_path) as z:
        man_raw = z.read("manifest.json")
        prev = [_sha(z.read(n)) for n in signature_files(z)]
    man = json.loads(man_raw)
    st = man.get("status") or {}
    if meaning == "approved":
        if st.get("conforming") is not True:
            raise ValueError(f"not approvable: the dossier lists {len(st.get('open_items') or [])} open item(s)")
        if any(s["meaning"] == "prepared" and _norm_name(s["signer"]).casefold() == name.casefold() for s in earlier):
            raise PermissionError("the person who prepared the dossier cannot also approve it")
    if len(prev) >= 99:
        raise ValueError("a dossier holds at most 99 signatures")
    payload = {
        "format": FORMAT, "manifest_sha256": _sha(man_raw), "previous": prev,
        "dossier": {"type": man.get("type") or "run", "plan": man.get("plan"), "project": man.get("project"),
                    "sample": man.get("sample"), "created_utc": man.get("created_utc"), "files": len(man.get("files") or [])},
        "status": {"conforming": st.get("conforming") is True, "open_items": len(st.get("open_items") or [])},
        "signer": {"name": entry["name"], "key_id": entry["key_id"], "role": entry.get("role") or ""},
        "meaning": meaning, "statement": MEANINGS[meaning], "signed_utc": _utc(),
    }
    record = {"payload": payload, "algorithm": "Ed25519", "public_key": entry["public_key"],
              "signature": base64.b64encode(key.sign(canonical(payload))).decode("ascii")}
    # written to a copy that is checked before it replaces the package: a failure leaves it untouched
    tmp = zip_path.with_name(zip_path.name + ".signing.tmp")
    shutil.copyfile(zip_path, tmp)
    try:
        with zipfile.ZipFile(tmp, "a", zipfile.ZIP_DEFLATED) as z:
            z.writestr(f"signatures/{len(prev) + 1:02d}.json", json.dumps(record, indent=1, ensure_ascii=False))
        after = check(tmp, reg)
        if len(after) != len(prev) + 1 or not all(s["valid"] for s in after):
            raise RuntimeError("the signed copy did not check out; the package was left unchanged")
        os.replace(tmp, zip_path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return after
