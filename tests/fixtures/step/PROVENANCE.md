Real artefacts written by SOLIDWORKS 2026 SP04.1 through the `mac` runtime on 2026-10-14
(`C:\New folder (4)\mecagent-alternative\work\`), copied unchanged:

| file | origin |
|---|---|
| prism.step / prism.report.json | `mac run specs/prism.json` — 60 x 40 x 20 mm block, analytic V = 48000 mm³ |
| prism_hole.step / prism_hole.report.json | `mac run specs/prism_hole.json` — same block with a Ø10 through hole |

prism_hole.report.json records two **failed** kernel checks (volume delta 0.085 mm³ against a
0.046 mm³ tolerance; area expectation omits the bore wall). They are kept as found: they are a
real failure case for the adapter to surface, not something to edit away.

Added 2026-10-17 — produced by **this adapter** (`astracad_cad build ... --authorize KM`) driving
SOLIDWORKS 2026 SP04.1 through `mac` on the user's PC (`astracad\_win\sw\`):

| file | origin |
|---|---|
| block_60x40x20.step / .report.json | "Create a 60 x 40 x 20 mm block in SolidWorks and export STEP" — task SUCCEEDED, 13/13 checks |
| shaft_d25_l100.step / .report.json | "SolidWorks shaft diameter 25 mm length 100 mm, export STEP" — kernel checks all passed; exposed the not_verifiable policy defect and the split-arc bounding-box flag |

Added 2026-10-17 (second batch) — `astracad_cad edit` → `mac edit` on SOLIDWORKS 2026 SP04.1,
editing a COPY of `_win\sw\block_60x40x20.SLDPRT` / `shaft_d25_l100.SLDPRT`; source SHA-256 unchanged:

| file | origin |
|---|---|
| block_depth35.step / .edit.json | D1@Boss-Extrude1 20 → 35 mm; kernel V = 84000 mm³ |
| shaft_l120.step | D1@Boss-Extrude1 100 → 120 mm |
