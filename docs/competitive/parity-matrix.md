# Parity matrix

Status per capability, using the ladder in `benchmark-methodology.md`. "Reference" columns record
what the vendor **documents** (D), what was **observed** (O), or UNKNOWN (U). Evidence IDs point to
`docs/reverse-engineering/evidence-register.md` (P6-xx) and
`reverse_engineering/MECAGENT_FEATURE_MATRIX.md` (F-xx).

| # | Capability | MecAgent | Pre6 | ASTraCAD status | Evidence / gap |
|---|---|---|---|---|---|
| C-01 | Auto-ballooning of 2D drawings | U | D (P6-03) | **VERIFIED** (DXF only) | balloons unique, non-overlapping, mapped; reopen 19/19 |
| C-02 | Inspection register PDF + XLSX | U | D (P6-05) | **VERIFIED** | `validate.py` reopen checks |
| C-03 | Scanned/raster drawing + OCR | U | D (P6-04) | NOT IMPLEMENTED | M4; refuses non-DXF input |
| C-04 | GD&T frames and datums | U | D (P6-03) | INVESTIGATED | escalated as uninterpreted, never guessed |
| C-05 | ISO 286 fit-class limits | U | U | **VERIFIED** (783 cells, 3–400 mm) | double-sourced table + IT check |
| C-06 | Missing-characteristic / coverage gate | U | D (P6-05, P6-10) | **VERIFIED** | release gate blocks with named reasons |
| C-07 | AS9102 FAIR forms | U | D (P6-05) | PLANNED | data model exists; form layout not built |
| C-08 | PPAP: PFMEA / Control Plan | U | D (P6-10) | NOT_INVESTIGATED (ours) | — |
| C-09 | SPC / instrument library | U | D (P6-06) | NOT IMPLEMENTED | — |
| C-10 | Revision comparison of drawings | U | D (P6-08) | **TESTED** (this session) | `astracad_inspect.revision` |
| C-11 | Tolerance stack WC/RSS/MC | U | U | **TESTED** (this session) | `astracad_tol` vs closed-form references |
| C-12 | Selective assembly | U | U | **TESTED** (this session) | `astracad_tol.selective` |
| C-13 | Explainable costing with sourced rate book | U | D (P6-08) | NOT IMPLEMENTED | M7 |
| C-14 | Live SolidWorks part creation + verification | D/O (F-01, F-16) | U | **VERIFIED** in `mac` (real SW 2026) | kernel read-back, analytic deltas ~1e-12 |
| C-15 | Modify dimension of an existing model | D (F-17) | U | **END_TO_END_VERIFIED** (on a copy, source hash proven unchanged) | block 20→35 (V 84000 kernel = STEP re-read), shaft 100→120, 2026-10-17 |
| C-16 | Python orchestrator → SolidWorks | — | — | **END_TO_END_VERIFIED** (create) | 3/3 live runs 2026-10-17 |
| C-17 | Creo / Fusion / AutoCAD automation | U (logos only) | — | DISCOVERY ONLY | adapters report state; operations return UNSUPPORTED_OPERATION |
| C-18 | Text → CAD | D (F-03) | — | PLANNED | structured spec exists; NL interpretation not built |
| C-19 | CAD → drawing (.SLDDRW) | D (F-06) | — | NOT IMPLEMENTED | — |
| C-20 | STEP → 3-axis features → G-code | — | D (P6-07) | NOT IMPLEMENTED | needs a real CAM backend |
| C-21 | Persistent background tasks | D (F-10) | U | **TESTED** (this session) | `astracad_core.tasks` (SQLite) |
| C-22 | Independent verification of every result | U | "every number traces to source" (P6-10) | **VERIFIED** | three-valued checks, provenance states |

No row is `PARITY_DEMONSTRATED` or `SUPERIORITY_DEMONSTRATED`: neither reference product has been
run on a shared task set. Where ASTraCAD is stricter by design (C-06, C-22: refuses to release a
plan with unresolved characteristics, and never invents limits), that is a **design difference**,
not a measured superiority.
