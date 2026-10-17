# Threat model (2026-10-17)

Scope: the Python package on the user's Windows PC, the `mac` subprocess, SOLIDWORKS, local files.
No network service is exposed; no model provider is called.

| Asset / boundary | Threat | Control in place | Gap |
|---|---|---|---|
| User's CAD session | An instruction (or generated plan) changes the live model unintentionally | Every op has a risk class; MUTATING/PERSISTING ops require an `Authorization` naming a person and op scope; without it the task parks in WAITING_FOR_APPROVAL | CLI `--authorize` is self-asserted (single-user tool); no OS-level identity |
| User's files | Overwrite or write outside intent | Output dir must be inside `allowed_output_dirs`; adapter refuses to overwrite existing outputs; source drawings are read-only and SHA-256 recorded | No sandbox user / ACL |
| Command execution | Injection via request text or spec | `mac` invoked with an argv list, `shell=False`; spec names restricted to `[A-Za-z0-9_-]{1,64}`; only registry-listed `$op` kinds pass validation | `mac` binary path comes from env/known locations — trusted |
| Untrusted DXF / STEP | Malformed or huge files exhaust memory / crash | ezdxf recover path, entity-level error capture; STEP reader has a 200 MB limit and refuses unknown units | No per-file CPU timeout on DXF parsing |
| Uploaded drawings (workspace) | A file that is not what its name says (e.g. an executable named `.dxf`), an oversized body, a path in the file name, a project id that walks out of the store | Extension must be a supported drawing type AND the leading bytes must match that type; 100 MiB cap checked before reading; names reduced to a plain file name (no folders, no device names); project ids `[a-z0-9-]`, resolved inside the store root; stored by SHA-256, read-only, never overwritten; every refusal drains the body then answers (no connection reset) | Not virus-scanned; the signature test proves the type, not that the content is benign - parsing still happens later on the preserved copy |
| Preserved originals | Edited outside ASTraCAD, then inspected as if original | Hash re-checked before every inspection; a mismatch is refused with the reason | A user with file-system rights can still change the read-only bit |
| Task records | Tampering with evidence | SQLite with append-only event log by convention | Not cryptographically chained |
| Generated code | AI-written code executed | None executed: the interpreter is deterministic; no model output is run | When a model gateway is added it must emit specs, never code |
| External providers | Confidential drawings sent out | No provider integration exists | Must be opt-in per project when added |
| Dependencies | Licence/supply chain | Pinned versions in `requirements.lock`; permissive PDF reader preferred over AGPL | No wheel hashes (needs network) |
