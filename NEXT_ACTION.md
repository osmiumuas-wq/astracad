# NEXT ACTION

## Done 2026-10-17: Project-level quality dossier (C-33)

**What was changed:**
- `src/astracad_inspect/dossier.py`: Added `project_status()`, `_project_pdf()`, and `build_project()`. Builds a project-level dossier ZIP bundling all runs (`runs/<stem>/...`), all serial results and FAI workbooks, all NCRs (`ncr/...`), all started checklists, and preserved source drawings (`sources/...`). Emits `dossier.pdf` with project-wide summary table and per-run breakdown, plus `manifest.json`. Adheres to strict honesty rule: `conforming: True` only if every run is releasable, every serial is acceptable, every NCR is closed/cancelled, and every checklist is signed.
- `src/astracad_web/server.py`: Added `Workspace.project_dossier(pid)` and endpoint `POST /api/project_dossier` (`{"project": "<pid>"}`). Gathers all runs, exports fresh workbooks/checklists, collects unique project NCRs and verified source paths, builds the ZIP, and re-verifies all hashes via `dossier.verify()`.
- `src/astracad_web/index.html`: Added "Quality dossier" action button in the project view header (`viewProject`) that calls `/api/project_dossier`, reports verification checks and conformance status via toasts, and opens the dossier ZIP. Preserved CRLF line endings.
- `tests/test_dossier.py`: Added 6 new unit & integration tests covering project dossier build, verification, manifest structure, honesty rules with open items vs conforming state, empty project rejection, HTTP API endpoint, and omitting modified/tampered source drawings.
- `CURRENT_STATE.md`, `parity-matrix.md`, `known-limitations.md`: Updated capability row C-33 and test baseline (520 passed).

**What was verified and how:**
- Baseline test suite before changes: 514 passed (187.57s).
- Web frontend syntax check: `py -m pytest -q tests/test_web.py` passed (14 passed, Node.js syntax check on `index.html`).
- Dossier test suite: `py -m pytest -q tests/test_dossier.py` passed (10 passed in 16.81s).
- Full test suite: `py -m pytest -q` passed completely: **520 passed**, 0 failed, 0 skipped (191.17s).
- Line ending integrity verified with python byte checks: `server.py`, `index.html`, `CURRENT_STATE.md`, `NEXT_ACTION.md` 100% CRLF; `dossier.py` and `test_dossier.py` 100% LF.

**What could not be verified:**
- Live browser click on the new button in a running browser session with real human interaction (verified via programmatic headless HTTP API integration test and Node syntax check).

## Done 2026-10-17: Text → CAD (constitution Part 6 / C-18)

    py -m astracad_cad build "SolidWorks 80 x 80 x 10 mm plate with 4 x 8 mm holes on a 50 mm bolt circle, centred, starting at 45 deg, export STEP" --out OUT --authorize KM
    py -m astracad_web --db DB --runs OUT   ->  Text to CAD

Seven shape requests built live in SOLIDWORKS 2026, each END_TO_END_VERIFIED (kernel volume = prediction,
STEP size exact; see CURRENT_STATE). Next here: fillets / chamfers on named edges (needs a way to name an
edge without guessing - e.g. "all outside vertical edges" resolved from the kernel and listed for
confirmation), slots and pockets, a circular-pattern op in `mac` (bolt circles are N separate holes today),
and the same requests through a second CAD system when one is installed.

Done 2026-10-17: open an existing SOLIDWORKS part and change a named dimension — live, END_TO_END_VERIFIED
(see `CURRENT_STATE.md`). The constitution's own example now runs when the request names the file
and the dimension:

    py -m astracad_cad build "change D1@Boss-Extrude1 of \"C:\path\shaft.SLDPRT\" to 25 mm" --out OUT --authorize KM
    py -m astracad_cad edit C:\path\shaft.SLDPRT --set D1@Sketch1=25 --name shaft_d25 --out OUT --authorize KM

Also done: a request that names only a word ("change the length of \"…\\shaft.SLDPRT\" to 120 mm")
now inspects the part read-only and lists candidate dimensions with ready commands; nothing is
selected (exit 3). Verified live on SW 2026, including sketch dimensions: "change the diameter of
shaft.SLDPRT to 30 mm" → pick D1@Sketch1 → edit → STEP re-read 30×30×100.

## 0b. Done 2026-10-17: enlarged verified corpus + reader fixes

All 22 exportable SOLIDWORKS sample drawings (29 sheets) with API answer keys that now record
visibility (hidden dims excluded), plus a separate generated tier (40 drawings, 416 dims,
`tools/make_generated_corpus.py`). Five reader fixes from what the corpus exposed; vector PDF went
from 90.8% / 85.7% to 97.5% / 96.7% recall / precision on the same keys. Remaining errors are named in
evaluation-results.md. All of it is still one CAD system's output: the next accuracy step is drawings
from other sources (AutoCAD, Creo, Inventor, hand-drafted, customer) - see 1.

## 0a. Done 2026-10-17: measurement results (C-09)

Inspection workspace → Sample + Instrument in the bar → select a balloon → `M`, type the value,
Enter (moves to the next balloon) → Measurements tab → Export results + FAI. Values are only ever
what an inspector typed; verdicts come from the drawing's own limits; rows without limits are
recorded but not judged; a sample against an open (not releasable) plan is never "acceptable".
Done next (same day): the instrument register (Instruments page) is checked at entry - verified
live: an expired caliper refused, 99.9964 from a 0.001 mm micrometer refused, 99.996 recorded.
Done next: results import (CSV/XLSX, long or wide, preview -> commit, traced to file hash + row,
file preserved). Next here, if CMM files are provided: a mapping profile per CMM program (feature
name -> balloon) saved with the project, so repeat imports need no renaming.

## 0. Done 2026-10-17: projects, upload, preserved originals (Part 9 items 1-3)

    py -m astracad_web --projects %USERPROFILE%\.astracad\projects --runs OUT
    → Projects → New project → drop drawings → Inspect

Uploads are checked by content, stored read-only by SHA-256 and re-hashed before each inspection;
runs and re-issues stay inside the project. **Easiest way to give me the real-drawing corpus below:
create a project and drop the drawings in** — they are preserved exactly as received.

## 1. Real-drawing corpus (M2) — highest priority

**Why:** every inspection accuracy figure so far comes from two machine-generated drawings. No claim
against Pre6 Lens ("near 100% on scanned drawings") or any production use is defensible without it.

**Needs from you:** 5–20 real DXF drawings you may use (your own parts, BAJA parts, college
drawings), copied into `astracad\_sources\corpus\`.

**Then:** label each (`tools/make_fixtures.py` label schema), run `py tools/regression.py`, publish the
per-drawing table (recall, precision, field accuracy, invented limits = 0) in
`docs/competitive/evaluation-results.md`, and fix every miss as notation-unsupported / geometry-unread
/ parser-bug.

## 2. Done 2026-10-17 (this pass)

- Vector-PDF ingestion (`ingest/pdf.py`), same bundle as DXF: `py -m astracad_inspect run drawing.pdf --units mm`.
- `mac` revolve (`sketch.profile` + `feature.revolve`) and linear pattern — exact kernel volumes, live.
- `py -m astracad_cad spec part.json --out OUT --authorize KM` runs any typed spec through the gate.
- Local workspace: `py -m astracad_web --db %USERPROFILE%\.astracad\tasks.sqlite3 --runs OUT` → http://127.0.0.1:8765/
- pypdfium2 installed and declared; PyMuPDF optional.
- Pre6 public-evidence pass from the PC (built-in browser, passive): rows P6-13…P6-22.

## 3. Competitor finding to act on

- Pre6 Lens example observed (P6-23…31, cookies accepted with your approval): OCR on a rasterised
  page, 73 dimensions + GD&T frames in ~10 s. Both gaps now have a first answer in ASTraCAD (section 5):
  GD&T frames are read, and scans go through two-engine OCR — but OCR recall is far below Lens on
  cluttered text, by design trading recall for zero accepted misreads. A fair accuracy comparison
  still needs the labelled M2 corpus.

## 4. Next engineering increments (no external dependency)

- Dimension the revolve profile (diameters of each step) so "change the diameter" works on revolved parts.
- PDF route: read the title block (revision, part number) and pair tolerance-table text to rows.
- `mac`: circular pattern, mirror, shell.
- Web workspace: show `astracad_tol` stack reports; trigger a word→dimension proposal (read-only).

## 5. Done 2026-10-17 (GD&T + scans)

- GD&T feature control frames from DXF `TOLERANCE` entities (+ Unicode frames in PDF/OCR text): one row per
  frame row, limits 0..zone, MMC/LMC bonus flagged, malformed frames escalated, frames drawn in the layout.
- Scanned drawings (.png/.jpg/.tif/.bmp, image-only PDFs): Windows OCR + RapidOCR must agree; disagreement
  is escalated with both readings. `py -m astracad_inspect run scan.png --units mm`.

## 6. Next to raise OCR recall (precision stays the gate)

- Remove circles/arcs crossing text (Hough) before re-reading a disagreed region.
- Pair stacked deviation runs to their nominal by position (as the PDF route does).
- Read a third engine only as a tie-breaker, never as a sole source.
- Score OCR recall/precision on the real-drawing corpus (still needs your DXF/PDF/scan files).

## 7. DONE 2026-10-17: SOLIDWORKS drawing generation → inspection loop (see CURRENT_STATE)

Kept for the record; the plan below is what was implemented.

Both trees are now git repos (`astracad` b110afc, `mecagent-alternative` 28e774c; local only, not pushed).

Add `mac drawing <part.SLDPRT> --out DIR --name N [--angle first|third]` in `mecagent-alternative/src/Mac/EditCommands.cs`:
- open the part read-only (reuse `Open`), hash before/after;
- template from `GetUserPreferenceStringValue(swDefaultTemplateDrawing)`;
- `IDrawingDoc.Create1stAngleViews2(partPath)` (ISO default) or `Create3rdAngleViews2`;
- `IDrawingDoc.InsertModelAnnotations3(swImportModelItemsFromEntireModel, swInsertDimensionsMarkedForDrawing, true, false, false, false)`;
- walk views (`GetFirstView`/`GetNextView`, `GetDisplayDimensions`) and report every dimension's full name and value;
- `Extension.SaveAs3` to .SLDDRW, .DXF and .PDF; write `<name>.drawing.json`.
Acceptance: every driving model dimension from `mac inspect` appears in the drawing with an equal value, and
`py -m astracad_inspect run <name>.dxf` passes its geometry cross-check on each one (an independent check of
the CAD drawing). Then add `Op.CREATE_DRAWING` to `astracad_cad/contract.py` and the SOLIDWORKS adapter.
After that: explainable costing module (`astracad_mfg`) with sourced, dated inputs (charter Part 12).

## 8. Next (highest value, no external dependency)

1. DONE 2026-10-17 — `py -m astracad_mfg cost --kernel-report R --step S --rates RATES --job JOB --out DIR`
   (see docs/examples/costing/*.EXAMPLE.json; placeholders, not prices). Original plan:
   **Explainable costing** (`astracad_mfg`, charter Part 12): machining cost from part volume/size (kernel or
   STEP re-read) and explicit, sourced, dated inputs (material price, machine/labour rates, setup, cycle,
   quantity, overhead, scrap). Every figure carries its formula and source; no invented prices.
2. Drawing route: add a sheet format/title block and tolerances (from a spec or ISO 2768 class chosen by the
   user) so the inspection plan has limits; investigate the one-off Model Items hang.
3. Dimension the revolve profile so revolved parts reach drawings with their diameters.

## 9. Done 2026-10-17: industrial-drawing benchmark (see CURRENT_STATE)

Next to raise it further:
1. Your own drawings into `_sources\corpus\` (any CAD), scored the same way — vendor samples are one style.
2. PDF: GD&T frames drawn as graphics (gtolwitnessline frame missed), remaining balloons on `leg`.
3. OCR route on the same sheets (see evaluation-results), then raise its recall with region re-reads.
