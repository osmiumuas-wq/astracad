# Evaluation results

Only recorded runs appear here.

| Date | Benchmark | Dataset | Result | Where |
|---|---|---|---|---|
| 2026-10-15 | Inspection extraction | 2 synthetic DXF (12 labelled chars) | recall 1.0, precision 1.0, field acc 1.0 | `_out/regression_report.json` (PC) |
| 2026-10-14 | SolidWorks part specs | 8 specs in `mecagent-alternative/specs/batch` | 8/8 jobs, analytic volume deltas ≤ 1e-11 mm³ on prism family | `mecagent-alternative/work/*.report.json` |
| 2026-10-17 | Tolerance engine | closed-form references in `tests/test_tol.py` | 19/19 pass; MC sigma within 1% of RSS closed form | `tests/test_tol.py` |
| 2026-10-17 | ASTraCAD -> SOLIDWORKS (real app) | 3 interpreted requests | 3/3 SUCCEEDED; block polyhedral V re-read = kernel = analytic 48000 mm³ | `_win/sw2/*.log` (PC) |

Synthetic results do not predict accuracy on production drawings (M2 corpus not yet acquired).
No reference-product run exists, so no comparative result exists.

## 2026-10-17 — Industrial drawings scored against the SOLIDWORKS API (first real-drawing benchmark)

**Dataset.** 13 drawings / 18 sheets shipped with SOLIDWORKS 2026 (`samples\tutorial`, `introsw`,
`sldBenchmarking`): mould bases, a punch holder, an AutoCAD-origin flange (7550-021, inches, toleranced
bore, thread and bolt-circle callouts), a food-processor housing, weldment legs, a universal beam.
Licensed sample files: kept in `_sources/corpus_sw` (git-ignored), not redistributed.

**Answer key.** `mac export-drawing` reads every display dimension and GD&T frame from the SOLIDWORKS API
(value in drawing units, kind, tolerance type and deviations) — independent of the exported DXF/PDF.

**Method.** `py tools/benchmark_sw.py` (rules in its docstring): a row matches a key dimension when kinds
are compatible and the value agrees within half a unit of the printed last digit; one-to-one, closest first.

| Drawing | Sheet | Key dims | DXF route | Vector-PDF route |
|---|---|---|---|---|
| acad_7550_021 | Model | 12 | 12/12 (12 rows; tol 1/1) | 12/12 (13 rows; tol 1/1) |
| acad_7550_021 | Layout1 | 13 | 13/13 (13 rows; tol 1/1) | 13/13 (14 rows; tol 1/1) |
| bezel_moldbase | Sheet1 | 10 | export failed | 10/10 (10 rows; tol 0/0) |
| box_intro | Sheet1 | 6 | 6/6 (6 rows; tol 0/0) | 6/6 (6 rows; tol 0/0) |
| can_intro | Sheet1 | 6 | 6/6 (6 rows; tol 0/0) | 5/6 (6 rows; tol 0/0) |
| carrier | Sheet1 | 0 | export failed | 0/0 (0 rows; tol 0/0) |
| cavity | Sheet1 | 4 | 4/4 (4 rows; tol 0/0) | 4/4 (4 rows; tol 0/0) |
| clamp1 | Sheet1 | 1 | 1/1 (1 rows; tol 0/0) | 1/1 (1 rows; tol 0/0) |
| foodprocessor | Sheet1 | 21 | 10/21 (12 rows; tol 0/0) | 8/21 (12 rows; tol 0/0) |
| foodprocessor | Sheet2 | 0 | 0/0 (0 rows; tol 0/0) | 0/0 (0 rows; tol 0/0) |
| foodprocessor | Sheet3 | 0 | 0/0 (0 rows; tol 0/0) | 0/0 (0 rows; tol 0/0) |
| foodprocessor | Sheet4 | 0 | 0/0 (0 rows; tol 0/0) | 0/0 (0 rows; tol 0/0) |
| gtolwitnessline | Sheet1 | 2 | 2/2 (2 rows; tol 0/0) | 2/2 (3 rows; tol 0/0) |
| leg | Sheet1 | 16 | 16/16 (16 rows; tol 0/0) | 14/16 (22 rows; tol 0/0) |
| punch_holder | Sheet1 | 0 | 0/0 (0 rows; tol 0/0) | 0/0 (0 rows; tol 0/0) |
| simplehole | Sheet1 | 0 | 0/0 (0 rows; tol 0/0) | 0/0 (0 rows; tol 0/0) |
| simplehole | Sheet2 | 0 | 0/0 (0 rows; tol 0/0) | 0/0 (4 rows; tol 0/0) |
| universal_beam | Sheet1 | 12 | 7/12 (7 rows; tol 0/0) | 7/12 (7 rows; tol 0/0) |

| Route | Recall (all key dims) | Precision | Tolerance accuracy | GD&T frames |
|---|---|---|---|---|
| DXF | 77/93 = 82.8% | 77/79 = 97.5% | 2/2 | 1/1 |
| Vector PDF | 82/103 = 79.6% | 82/102 = 80.4% | 2/2 | 0/1 |

**Reading the recall figure.** 14 key dimensions never reach the exported files (universal_beam: 5 —
SOLIDWORKS exports 7 of 12; foodprocessor: the DXF holds 12 of 21). Against dimensions present in the
DXF the route finds 77/79 = 97.5%; the 2 unmatched are the sample's own drafting errors below.

**Starting point the same day** (before the fixes this benchmark drove): DXF recall 61.3% / precision 72.2%;
PDF recall 69.9% / precision 19.3%; tolerances 0/2 (PDF).

**Defects the benchmark exposed, all fixed with regression tests (`tests/test_industrial_patterns.py`):**
1. `%%c<>` / `(<>)` (measured value with prefix) read literally -> now the rendered block text, tolerance pieces included.
2. A sheet's own drafting error was accepted silently: foodprocessor prints **15** for a 150 mm span and
   **⌀0.92** for ⌀9.2 (DIMLFAC 0.1 on a 1:1 sheet). Now a blocking `dimension_scale_factor_outlier` that states the true measurement.
3. PDF: border zone labels, balloon item numbers and table cells were 290 of 302 false rows -> classified from the sheet's vector lines.
4. PDF: SOLIDWORKS draws ⌀ as vector graphics -> detected (ring + slash) and recorded as such on the row.
5. PDF: stacked tolerances at the same font size, and callouts at arbitrary angles, were not joined.
6. A dimensions-only DXF crashed the extents step.

**Not shown here:** the OCR route on these sheets (run separately), and any comparison with Pre6 Lens or
MecAgent — no product was run on this dataset, so no comparative claim is made. Answer keys are the CAD
model's dimensions; a printed value that legitimately differs from the model (rounding to fewer decimals)
is matched by the half-digit rule, a deliberately overridden text is not.

### Same benchmark, later the same day: PDF frames and the OCR route

| Route | Recall | Precision | Tolerances | GD&T frames | Time (18 sheets) |
|---|---|---|---|---|---|
| DXF | 82.8% | 97.5% | 2/2 | 1/1 | seconds |
| Vector PDF (+ frames drawn as graphics) | 79.6% | 81.2% | 2/2 | 1/1 | seconds |
| OCR, before sheet geometry | 30.1% (31/103) | 28.4% (31/109) | 0/0 | 0/1 | ~75 min |
| OCR, with sheet geometry from pixels | 26.2% (27/103) | 79.4% (27/34) | 0/0 | 0/1 | 14.5 min |

* OCR precision rose because balloons, zone labels and table cells are now classified from rules and
  circles recovered from the image. **No OCR row in either run was a misread dimension value**: every
  unmatched row was real text of another kind (balloon numbers, BOM/hole-table/title-block values,
  a frame value). The two-engine consensus held on real sheets.
* OCR recall fell by 4 dimensions with the classifier and the 40-re-read cap; recall on cluttered
  vector-origin sheets is the OCR route's weakest point and is not competitive with a dedicated AI
  reader such as Pre6 Lens (observed 73 dims on its own sample, P6-26) — stated, not hidden.
* Sheets were rendered from the exported PDFs at 300 dpi: clean "scans". Real scans (skew, noise,
  stamps) are expected to be harder; no real scanned drawing has been measured.
