# Signed quality dossiers (2026-10-18)

A dossier signature is a named person's **Ed25519** signature over the dossier's `manifest.json`, which lists
every other file in the package with its SHA-256. Change one byte of any file, add a file, or touch the
manifest, and the signature no longer checks out.

## What a signature says, and what it does not

Each signature carries one of three fixed statements (`astracad_inspect.signing.MEANINGS`):

| Meaning | Statement | Rule |
|---|---|---|
| prepared | "I prepared this dossier; it holds the records its manifest lists." | - |
| reviewed | "I reviewed the records in this dossier." | - |
| approved | "I approve these records; they show conformance with no open item." | only when the manifest states conformance; never by the person who signed it as prepared |

It does **not** say that every file was read; it does not prove *when* it was made (the time is the signing
PC's clock, not a trusted timestamp); and it identifies the signer only as far as the register entry that
names the key. One person may sign each meaning once.

## Where things are kept

| What | Where | Contains |
|---|---|---|
| Private key | `%USERPROFILE%\.astracad\keys\<key id>.pem` (workspace `--keys`), never in a project or run folder | PKCS#8, encrypted with the signer's passphrase (`cryptography`'s best available encryption); written once, never overwritten |
| Register | `signers.json` beside `instruments.json` | name, role, key id, public key, made / revoked (by whom, why) + an append-only log; **no private material** |
| Signatures | `signatures/01.json`, `02.json`, ... inside the dossier ZIP | see below; the files the manifest lists are not changed by signing |

The passphrase is never stored or logged. If it is lost, the key is revoked and a new one made. A revoked key
cannot sign; signatures it made earlier still check out and say "revoked later".

## Format `astracad-dossier-signature/1`

```json
{
 "payload": {
  "format": "astracad-dossier-signature/1",
  "manifest_sha256": "<sha256 of manifest.json as stored in the ZIP>",
  "previous": ["<sha256 of signatures/01.json>", "..."],
  "dossier": {"type": "run|project", "plan": "...", "project": "...", "sample": "...", "created_utc": "...", "files": 30},
  "status": {"conforming": false, "open_items": 3},
  "signer": {"name": "...", "key_id": "<first 16 hex of sha256(public key)>", "role": "..."},
  "meaning": "prepared|reviewed|approved",
  "statement": "<the fixed statement for the meaning>",
  "signed_utc": "2026-10-18T05:15:01Z"
 },
 "algorithm": "Ed25519",
 "public_key": "<base64, 32 raw bytes>",
 "signature": "<base64, 64 bytes>"
}
```

The signed bytes are `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` encoded
as UTF-8. `previous` lists every earlier signature file's SHA-256 in order, so a signature cannot be removed,
changed or reordered without the later ones failing.

## Checking a dossier

With ASTraCAD (any PC):

    py -m astracad_inspect dossier verify <dossier.zip> [--register signers.json]

`TRUSTED` = the signature checks out and the key is in the given register under that name and was not revoked
when it signed; `VALID` = it checks out, but the key is not known here: compare the key id with the signer
(phone, letter) before relying on it; `BAD` = it does not check out, with the reason. Exit code 0 only when
the package and every signature check out.

Without ASTraCAD, any Ed25519 implementation will do, e.g. Python `cryptography`:

```python
import base64, hashlib, json, zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
z = zipfile.ZipFile("dossier.zip")
man = z.read("manifest.json")
for f in json.loads(man)["files"]:                       # 1. every listed file is as signed
    assert hashlib.sha256(z.read(f["path"])).hexdigest() == f["sha256"], f["path"]
sig = json.loads(z.read("signatures/01.json"))           # 2. the signature is over this manifest
p = sig["payload"]
assert p["manifest_sha256"] == hashlib.sha256(man).hexdigest()
Ed25519PublicKey.from_public_bytes(base64.b64decode(sig["public_key"])).verify(
    base64.b64decode(sig["signature"]),
    json.dumps(p, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
print(p["meaning"], "by", p["signer"]["name"], "key", p["signer"]["key_id"])
```

(also check that no file outside the manifest, other than `signatures/`, is in the ZIP, and the `previous` chain.)

## Signing

Workspace: build the dossier (Quality dossier, on an inspection or a project) - the dossier window shows the
package checks and every signature - then choose the meaning, type the passphrase, Sign. Keys are made and
revoked on the **Signing keys** page. Command line:

    py -m astracad_inspect dossier enrol --signer "K M" [--role "Quality"]
    py -m astracad_inspect dossier sign <dossier.zip> --signer "K M" --meaning reviewed
    py -m astracad_inspect dossier revoke --key-id <id> --by "Q Lead" --reason "passphrase lost"

A signature is refused - and the package left byte-for-byte as it was - when the package fails its own checks,
an earlier signature does not check out, the passphrase does not open the signer's registered key, or the
meaning's rule is not met. Rebuilding a dossier never overwrites a signed one: the rebuild is written as
`..._v2.zip`.
