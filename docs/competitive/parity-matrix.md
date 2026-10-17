# Parity matrix

Status per capability, using the ladder in `benchmark-methodology.md`. "Reference" columns record
what the vendor **documents** (D), what was **observed** (O), or UNKNOWN (U). Evidence IDs point to
`docs/reverse-engineering/evidence-register.md` (P6-xx) and
`reverse_engineering/MECAGENT_FEATURE_MATRIX.md` (F-xx).

| # | Capability | MecAgent | Pre6 | ASTraCAD status | Evidence / gap |
|---|---|---|---|---|---|
| C-01 | Auto-ballooning of 2D drawings | U | D/O (P6-03, P6-26) | **VERIFIED** (DXF, vector PDF, scans) | 18 real SOLIDWORKS sheets: DXF recall 82.8% / precision 97.5%, PDF 79.6% / 81.2% (evaluation-results) |
| C-02 | Inspection register PDF + XLSX | U | D (P6-05) | **VERIFIED** | `validate.py` reopen checks |
| C-03 | Scanned/raster drawing + OCR | U | D/O (P6-04, P6-25) | **TESTED** (two-engine consensus) | 18 sheets at 300 dpi: recall 43.7%, precision 80.4%, no misread value accepted; far below Lens on recall |
| C-04 | GD&T frames and datums | U | D/O (P6-03, P6-27) | **TESTED** | DXF TOLERANCE entities; PDF text frames and frames drawn as graphics (14 symbols by strokes); 1/1 real frame; MMC/LMC bonus not computed |
| C-05 | ISO 286 fit-class limits | U | U | **VERIFIED** (783 cells, 3–400 mm) | double-sourced table + IT check |
| C-06 | Missing-characteristic / coverage gate | U | D (P6-05, P6-10) | **VERIFIED** | release gate blocks with named reasons |
| C-07 | AS9102 FAIR forms | U | D (P6-05) | **TESTED** | `inspect fai PLAN.json`: Forms 1-3 structure, zones from the sheet's border labels, results/serials/org data left blank (never invented); reopen-verified |
| C-08 | PPAP: PFMEA / Control Plan | U | D (P6-10) | NOT_INVESTIGATED (ours) | — |
| C-09 | SPC / instrument library | U | D (P6-06) | NOT IMPLEMENTED | — |
| C-10 | Revision comparison of drawings | U | D (P6-08) | **TESTED** (this session) | `astracad_inspect.revision` |
| C-11 | Tolerance stack WC/RSS/MC | U | U | **TESTED** (this session) | `astracad_tol` vs closed-form references |
| C-12 | Selective assembly | U | U | **TESTED** (this session) | `astracad_tol.selective` |
| C-13 | Explainable costing with sourced rate book | U | D (P6-08) | **TESTED** | `astracad_mfg`: CAD-evidence geometry, sourced+dated inputs, refusals; vs hand calculation |
| C-14 | Live SolidWorks part creation + verification | D/O (F-01, F-16) | U | **VERIFIED** in `mac` (real SW 2026) | kernel read-back, analytic deltas ~1e-12 |
| C-15 | Modify dimension of an existing model | D (F-17) | U | **END_TO_END_VERIFIED** (on a copy, source hash proven unchanged) | block 20→35 (V 84000 kernel = STEP re-read), shaft 100→120, 2026-10-17 |
| C-16 | Python orchestrator → SolidWorks | — | — | **END_TO_END_VERIFIED** (create) | 3/3 live runs 2026-10-17 |
| C-17 | Creo / Fusion / AutoCAD automation | U (logos only) | — | DISCOVERY ONLY | adapters report state; operations return UNSUPPORTED_OPERATION |
| C-18 | Text → CAD | D (F-03) | — | PLANNED | structured spec exists; NL interpretation not built |
| C-19 | CAD → drawing (.SLDDRW) | D (F-06) | — | **END_TO_END_VERIFIED** | `mac drawing` + independent DXF inspection; 3 live parts; no title block/tolerances yet |
| C-23 | Accuracy measured against an independent answer key | U | claims "near 100%" (P6-03), no method | **TESTED** | SOLIDWORKS API answer keys, 13 drawings / 18 sheets, method in `tools/benchmark_sw.py` |
| C-26 | Drawing zone (grid) references | U | D (P6-20) | **TESTED** | PDF and scan routes, from the sheet's own border labels; DXF not yet |
| C-25 | General tolerances (ISO 2768 / title-block rules) | U | D (P6-19: scans tolerance tables) | **TESTED** | 24 tests incl. table values and refusals; table values transcribed, to verify against a licensed ISO copy |
| C-24 | Detects a drawing's own drafting errors | U | U | **TESTED** | found a SOLIDWORKS sample printing 15 for a 150 mm span (blocking) |
| C-20 | STEP → 3-axis features → G-code | — | D (P6-07) | NOT IMPLEMENTED | needs a real CAM backend |
| C-21 | Persistent background tasks | D (F-10) | U | **TESTED** (this session) | `astracad_core.tasks` (SQLite) |
| C-22 | Independent verification of every result | U | "every number traces to source" (P6-10) | **VERIFIED** | three-valued checks, provenance states |

No row is `PARITY_DEMONSTRATED` or `SUPERIORITY_DEMONSTRATED`: neither reference product has been
run on a shared task set. Where ASTraCAD is stricter by design (C-06, C-22: refuses to release a
plan with unresolved characteristics, and never invents limits), that is a **design difference**,
not a measured superiority.
