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

* Later still: item-balloon detection by circle fit (each radius band fitted on its own) removed
  the 6 BOM item numbers read as dimensions on "leg"; the vector-PDF route is now **78.6% recall /
  86.2% precision** (81/103, 81/94). One real dimension is lost: on "box_intro" the "95" is printed
  inside a tight drawn circle with nothing else in it, which geometry alone cannot tell from an item
  balloon; it is counted in the census as a balloon item number, not dropped silently.

### Balloon layout quality (`tools/benchmark_layout.py`, same corpus, 22 sheets with balloons)

| Layout | Balloons | Crossing leaders | Balloons on ink | Unplaced | Mean leader (diameters) | Numbering path |
|---|---|---|---|---|---|---|
| Ring around the content, greedy by cid (before) | 177 | 8 | 12 | 5 | 8.39 | 0.67 |
| Adjacent to the callout on measured clear paper, uncrossed, reading order | 175 | **0** | **0** | **0** | **0.20** | **0.50** |

Ink is measured on the balloon-free rendered sheet (dark pixels under the disc > 3 %). The inch
drawings also had 4 mm treated as 4 *inches* before, which is where the unplaced balloons came from.

* OCR precision rose because balloons, zone labels and table cells are now classified from rules and
  circles recovered from the image. **No OCR row in either run was a misread dimension value**: every
  unmatched row was real text of another kind (balloon numbers, BOM/hole-table/title-block values,
  a frame value). The two-engine consensus held on real sheets.
* OCR recall fell by 4 dimensions with the classifier and the 40-re-read cap; recall on cluttered
  vector-origin sheets is the OCR route's weakest point and is not competitive with a dedicated AI
  reader such as Pre6 Lens (observed 73 dims on its own sample, P6-26) — stated, not hidden.
* Sheets were rendered from the exported PDFs at 300 dpi: clean "scans". Real scans (skew, noise,
  stamps) are expected to be harder; no real scanned drawing has been measured.

### OCR route, decimal-point rule (same day)

Windows OCR reads an AutoCAD stroke font's decimal point as '1' ('11345' for '1.345'). Accepting '.'
where RapidOCR explicitly read it, and only when the '1'-reading has no decimal point at all, raised OCR
recall from 26.2% to **43.7%** (45/103) at **80.4%** precision (45/56) on the same 18 sheets. Unmatched rows
are values SOLIDWORKS stores as notes (bolt-circle and chamfer callouts), balloons and a frame value —
still no misread dimension. Stacked tolerances read as separate regions are joined when both agree; when
one does not, the row is blocked (`ocr_adjacent_tolerance_unread`) instead of appearing untoleranced.

### Simulated scans (same day)

`tools/degrade.py` turns each sheet into a SIMULATED scan (rotation 0.5-1.5 deg, grey paper, noise,
blur, JPEG 55-75), deterministic per sheet; parameters are written next to each image. Not real scans.

| OCR route on | Recall | Precision |
|---|---|---|
| clean 300 dpi renders | 43.7% (45/103) | 80.4% |
| simulated scans, no deskew | 42.7% (44/103) | 80.0% |
| simulated scans, with deskew | 42.7% (44/103) | 83.0% |

Deskew measures within 0.021 deg of the true rotation (5 sheets x 4 angles) but did not change recall:
both engines already tolerate ~1 deg. The OCR bottleneck is two-engine agreement on cluttered text,
not page geometry. Real scans (stamps, folds, hand marks, low dpi) remain unmeasured.

### OCR route, second confusion round (same day)

From all 109 unresolved OCR readings on the 18 sheets: (1) RapidOCR's text angle is kept and
angled callouts are levelled before BOTH engines re-read them (Windows OCR missed every callout on an
angled leader); (2) hatching read as runs of '1' is counted, not sent to review; (3) degree sign read
'0' by one engine and '*' by the other, last position only; (4) a decimal point dropped by one engine;
(5) Greek phi accepted as an explicit diameter sign ('D' is not). Clean renders: recall 43.7% ->
**50.5%** (52/103), precision **81.3%**, 12 min for 18 sheets. Extra rows remain real non-dimension
text (chamfer and bolt-circle callouts, balloons, a frame value).
