# Dependency and licence register (2026-10-17)

Python dependencies and their licences are recorded in detail in
`docs/licences/dependency-licences.md` (written 2026-10-15). Changes since then:

| Component | Licence | Use | Status |
|---|---|---|---|
| pypdfium2 (PDFium) | Apache-2.0 / BSD-3-Clause | Preferred PDF read-back in `validate.py` | Optional extra `pdf-permissive`; present in the cloud workspace, **not yet installed on the PC** |
| pypdfium2 5.14.0 | Apache-2.0 / BSD-3 | Declared PDF reader: report read-back + vector-PDF ingestion | Installed on the PC 2026-10-17; `doctor` confirms it is the live reader |
| PyMuPDF | AGPL-3.0 or Artifex commercial | Optional `table-rebuild` extra (ISO table rebuild tool); run-time fallback only if pypdfium2 is missing | No longer a declared run-time dependency |
| sqlite3 | Python stdlib (public-domain SQLite) | Task store | — |
| SOLIDWORKS API / interop DLLs | Dassault Systèmes licence; API use bound to a licensed seat | Through `mac` (C#) only | Interop DLLs are referenced, not redistributed |
| REA (`rea-agents` 6.4.0) | MIT | Investigation tool on the PC; not a runtime dependency | — |
| MecAgent, Pre6 | Proprietary | Reference products — studied via public pages only; no code, text or assets copied | — |

PTC Creo TOOLKIT, Autodesk Fusion API and AutoCAD ActiveX each carry vendor terms; none is used yet.
