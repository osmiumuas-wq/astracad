# ASTraCAD Ω — CURRENT STATE (2026-10-17)

Every number below was produced by a command run on 2026-10-17. Detail: `docs/architecture/current-state.md`.

## Modules (one modular monolith, `src/`)

| Package | What it does | Maturity |
|---|---|---|
| `astracad_inspect` | DXF, vector PDF **or a scan (two-engine OCR)** → characteristics incl. **GD&T frames** → ISO 286 limits → balloons → PDF/XLSX/JSON → reopen-and-verify; revision comparison; **sheet grid zone references (e.g. "B6") on DXF drawings** | Prototype, verified on 2 synthetic drawings (+ a vector-PDF rendering of one) and SOLIDWORKS corpus |
| `astracad_cad` | Versioned CAD adapter contract (12 connection states, 16 ops, risk classes), permission-gated orchestrator, **SOLIDWORKS adapter over `mac`** (now with title block & general tolerance forwarding and verification), discovery-only Creo/Fusion/AutoCAD, kernel-independent **STEP re-reader** | SOLIDWORKS create: END_TO_END_VERIFIED on the real app; others discovery only |
| `astracad_core` | Persistent SQLite task engine (8 states, bounded retry, irreversible-step guard, restart recovery); deterministic request interpreter with provenance per fact (**Text → CAD**: blocks, discs / flanges, stepped shafts, tubes / washers, hole rows, bolt circles, rectangular pockets and slots; "Text to CAD" page in the workspace); **projects** (`projects.py`): uploaded drawings preserved by SHA-256, read-only, type checked by content, re-hashed before every inspection | Tested (17 project tests incl. disguised files, truncation, traversal, tampering) |
| `astracad_inspect.measure` | **Measurement results**: inspector-entered values per sample (serial) and characteristic, append-only log, PASS/FAIL/NOT_EVALUABLE from the plan's limits, sample states, Cp/Cpk with n; results XLSX and AS9102 Form 3 results, re-opened and verified. In the workspace: Sample/Instrument in the review bar, Measure section per balloon (type, Enter, next balloon), Measurements matrix, export. **Instrument register** (Instruments page): calibration state, refusals at entry (unregistered, retired, expired, outside range, finer than resolution), 10:1 warning, instrument snapshot stored with each value | Tested (19) + used live |
| `astracad_web` | Local workspace (stdlib HTTP, 127.0.0.1 only): CAD status, task history, inspection runs, change sets; run an inspection; **review** (approve / reject / correct / dismiss, named + audit log) and **re-issue** as a new linked run — the reviewed plan is never edited | Tested (13 web + 13 review tests incl. host/CSRF/traversal/413 refusals); **Projects**: create a project, drag-and-drop upload with progress, inspect the preserved copy (runs + re-issues stay in the project, `run.json` names project + SHA-256), download the original; verified live with the SOLIDWORKS 7550-021 DXF/PDF; **redesigned UI** (design system in `docs/design-system/MASTER.md`): interactive pan/zoom drawing with balloons drawn from the plan, linked list + inspector (tolerance band vs geometry, checks, history), command palette, keyboard review (J/K/A/R/C), light/dark; viewed live at 1440, 1024 and 375 px |
| `astracad_inspect.dossier` | Quality dossier per run / serial OR per project: ZIP of every record + cover PDF + SHA-256 manifest, re-verified after writing; honest conformance statement with open items; "Quality dossier" button on each inspection and project page. Project dossier aggregates all runs, serials, re-issues, checklists, NCRs and preserved source drawings | Tested (10) + used live |
| `astracad_inspect.signing` | **Signed dossiers**: Ed25519 signatures over the dossier manifest (prepared / reviewed / approved; approval only when conforming and by a second person), chained; keys encrypted with the signer's passphrase in `~/.astracad/keys`, register of public keys beside the instrument register; revocation; a signed dossier is never overwritten by a rebuild; workspace dossier window + Signing keys page; `py -m astracad_inspect dossier verify\|sign\|enrol\|revoke`; format in docs/engineering/dossier-signatures.md | Tested (15) + used live 2026-10-18 on the 7550-021 project dossier with a test key (wrong passphrase refused, signed, tampered copy BAD, revoked key still checks earlier signature) |
| `astracad_inspect.ncr` | Nonconformance reports: raised from failed values / checklist items, containment, disposition (two-person rule for use-as-is / repair), closure with root cause, corrective action and verification; Nonconformances page; NCR numbers flow into the FAI | Tested + used live (NCR-2026-0001 raised, self-approval refused, approved, closed, FAI Form 3 col 11 filled) |
| `astracad_mfg.metrology` | True position (MMC/LMC bonus), three-wire thread, sine bar, bolt circle, cutting time, unit conversion — on the Machining page | Tested |
| `astracad_inspect.import_results` | CMM / gauge / spreadsheet results import (CSV, XLSX; long or wide): preview, per-row validation incl. instrument register, all-or-nothing commit unless partial is chosen, traceable to file hash + row, file preserved; **saved CMM mapping profiles per program with the project** for repeat imports without renaming; Measurements tab "Import results" with profile selector and new profile creation | Tested (9) + used live |
| `astracad_inspect.checklists` | Evidence-linked checklists (drawing review, FAI readiness per serial, setup / first-off) **plus custom templates** built on the Checklist templates page: check, numeric with limits (judged by ASTraCAD), text and automatic items; versioned, instances pinned to their version; named decisions, sign-off rules, XLSX export | Tested (15) + used live |
| `astracad_mfg.machining` | 12 machining calculations with equation, units, assumptions and warnings; no built-in material data; Machining page in the workspace; escalated roughness values link to "max turning feed for this Ra" | Tested (11) + used live |
| `astracad_mfg` | Explainable machining cost: geometry from CAD evidence (kernel volume + STEP re-read bbox, both hashed), every rate sourced and dated, nothing defaulted, 13 traced lines, JSON+XLSX re-read | Tested against a hand calculation (12 tests); example rate book is placeholders only |
| `astracad_tol` | Worst-case / RSS / Monte Carlo stacks with explicit distributions, ISO 286 fits, selective assembly (plan + measured matching) | Tested against closed-form references |
| `mecagent-alternative/` (C#, sibling tree) | `mac` runtime: live SOLIDWORKS COM automation; revolve (`sketch.profile` + `feature.revolve`), linear & circular pattern (`feature.pattern.circular`), rectangular cuts/pockets (`sketch.rectangle` + `feature.cut`), **title block & general tolerance note in `mac drawing`** (unstated revision / tolerance printed as NOT STATED / omitted, never defaulted), **fillets / chamfers on edges found by geometry** (`select`: corners / end_circles), a minimized SOLIDWORKS window restored for drawings | Executed on SW 2026 SP04.1; `mac selftest` 46/46 |

## Test baseline

| Where | Command | Result |
|---|---|---|
| Windows PC (Python 3.14.7) | `py -m pytest -q` | **577 passed**, 0 failed, 0 skipped (2026-10-18: after signed dossiers; pass `--basetemp` outside the repo if `.pytest_tmp` cannot be removed) |
| Windows | `py tools/benchmark_layout.py` | 22 real SOLIDWORKS sheets, 175 balloons: **0 crossing leaders, 0 balloons on drawing ink, 0 unplaced**, mean leader 0.20 balloon diameters (before: 8 / 12 / 5 / 8.39) |
| Windows | `py tools/regression.py` | 3 labelled synthetic fixtures (incl. GD&T): 20/20 characteristics, 0 wrong fields, 0 invented, 57/57 export checks |
| Windows | `py -m astracad_inspect run tests\fixtures\bracket_gdt_synthetic.dxf` | 5 frame rows (position ⌀0.05 MMC A\|B\|C, 2-row ⊥/∥, flatness, profile) + 3 dims; malformed frame escalated; frames drawn in the layout; 19/19 exports |
| Windows | `py -m astracad_inspect run tests\fixtures\plate_synthetic_scan300.png --units mm` | 3 of 9 callouts accepted (all correct: 100 ±0.1, ⌀20 H7, 45°), 7 escalated with both engines' readings, **0 wrong values**; ~25 s/page |
| Windows | `mac selftest` | 46/46 (end-edge fillet closed form = numeric integration, end chamfer Pappus, edge selector names checked; unstated revision prints NOT STATED; Pappus revolve vs closed-form cylinder, linear & circular patterns = seed × count, rectangular pocket volume and area derived analytically, drawing title block & general tolerance note formatters, profile refusals, non-finite kernel values written as null) |
| Cloud workspace (Python 3.13) | `python3 -m pytest -q` | 224 passed, 29 skipped (ISO-table rebuild sources only on the PC) |
| Windows | `py -m astracad_inspect selftest` | 34/34 |
| Windows | `py -m astracad_inspect run tests\fixtures\plate_synthetic.dxf` | 19/19 export reopen checks, exit 3 (delivered, correctly NOT releasable) |
| Windows | `py -m astracad_inspect run tests\fixtures\plate_synthetic.pdf --units mm` | every callout the DXF route resolves (9) read with the same kind, nominal and limits; stacked + rotated tolerances joined; 19/19 export checks; every row needs review (no geometry cross-check from a PDF) |

## Industrial-drawing accuracy (first real benchmark, 2026-10-17)

13 SOLIDWORKS sample drawings / 18 sheets (mould bases, punch holder, AutoCAD-origin flange in inches,
food-processor housing, weldments), scored against dimensions read from the SOLIDWORKS API
(`mac export-drawing` -> `py tools/benchmark_sw.py`; detail in docs/competitive/evaluation-results.md):

| Route | Recall | Precision | Tolerances | Same-day start |
|---|---|---|---|---|
| DXF | 82.8% (97.5% of dims present in the file) | 97.5% | 2/2 | 61.3% / 72.2% |
| Vector PDF | 78.6% | 86.2% (item-balloon detection by circle fit; was 79.6% / 81.2%) | 2/2 (+GD&T frames drawn as graphics 1/1) | 69.9% / 19.3% |
| **Enlarged corpus, printed dims only** (22 drawings / 29 sheets; 15 SOLIDWORKS-hidden dims excluded) | DXF **98.2%** / **98.2%**; vector PDF **97.5%** / **96.7%** (was 90.8% / 85.7% on the same keys) | tolerances 2/2, GD&T 2/2 | generated tier (40 drawings, 416 dims, reported separately): DXF 99.8% / 100%, PDF 99.5% / 99.8% | |
| OCR (scan @300 dpi) | 50.5% | 81.3% | unread stacks block the row | 30.1% / 28.4% |
| OCR on SIMULATED scans (+deskew) | 42.7% | 83.0% | — | — |

Found a real drafting error in a SOLIDWORKS sample (prints 15 for a 150 mm span) — now a blocking finding.

**General tolerances** (`--general-tolerance iso2768-m` or a statement read from the drawing, incl. title-block
notes inside block references): untoleranced linear/diameter/radius rows get limits from ISO 2768-1 or a
decimal-place rule, as a distinct `general` tolerance; angles, inch drawings (ISO), off-table sizes and
unstated place counts are refused with the reason. Blank template lines ("TWO PLACE DECIMAL ±") state nothing.

## Real-application evidence (SOLIDWORKS 2026 SP04.1, 2026-10-17)

`astracad_cad build "<request>" --out _win\sw2 --authorize KM`:

| Request | Task | Kernel | Independent STEP re-read |
|---|---|---|---|
| 60 x 40 x 20 mm block | SUCCEEDED | V = 48000.000000 mm³ | size 60×40×20, V = 48000.000000 (polyhedral) |
| shaft Ø25 x 100 mm | SUCCEEDED | V = 49087.385212 mm³ | size 25×25×100 exact; volume not_verifiable (curved) |
| block + Ø10 centred hole | SUCCEEDED | V = 46429.288889 (mac's own ~2 ppm through-all allowance) | size ok; volume not_verifiable |

**Fillets and chamfers on named edges** (2026-10-18, `build ... --authorize KM` into `_out\text2cad_edges`; `mac` runs of
the bare specs in `_out\fillet_probe_20261018`):

| Request (abridged) | Task | Kernel volume = prediction | Independent STEP re-read |
|---|---|---|---|
| plate 100x60x10, R5 corners, 4 x Ø6 at 20 pitch centred | SUCCEEDED | 58654.424808 | 10×60×100 exact |
| shaft Ø20 x 50, 1 mm chamfer on both ends | SUCCEEDED | 15647.225810 (Pappus) | 20×20×50 exact |
| stepped shaft Ø25 x 40 + Ø35 x 20, both ends R2 | SUCCEEDED | 38720.222084 (Pappus, ends R 12.5 / 17.5) | 35×35×60 exact |
| flange Ø120 x 12, 6 x Ø10 on 90 PCD, 1 mm chamfer both ends | SUCCEEDED | 129687.039135 | 12×120×120 exact |
| block 60x40x20, 5 mm chamfered corners, 20 x 10 x 5 pocket centred | SUCCEEDED | 46000.000000 | 20×40×60; V 46000 (polyhedral) |

`mac` alone: cylinder chamfer / fillet, stepped shaft chamfer / fillet (unequal ends), tube outer-edge chamfer (bore
left alone) - volume and area equal to the closed forms within 1e-11; the fillet's closed form is also checked against
a numeric integration in `mac selftest` (46/46).

**Text → CAD** (2026-10-17, `astracad_cad build "<request>" --out _out\text2cad --authorize KM`, and once from the
workspace "Text to CAD" page, task T-6cbfcf81a9ec):

| Request (abridged) | Task | Kernel volume vs prediction | Independent STEP re-read |
|---|---|---|---|
| stepped shaft dia 20 x 50, dia 30 x 10, dia 20 x 40 mm | SUCCEEDED | 35342.917353 = 35342.917353 | 30×30×100 exact |
| washer OD 30 ID 16 thickness 3 mm | SUCCEEDED | 1517.389252 = 1517.389252 | 3×30×30 exact |
| tube OD 40 wall 3 length 120 mm | SUCCEEDED | 41846.014146 = 41846.014146 | 40×40×120 exact |
| flange Ø120 x 12, 6 x Ø10 on 90 PCD | SUCCEEDED | 130061.935859 = 130061.935859 | 12×120×120 exact |
| plate 100x40x10, 4 x Ø6 at 20 pitch along the width, centred | SUCCEEDED | 38869.026645 = 38869.026645 | 10×40×100 exact |
| plate 80x80x10, 4 x Ø8 on Ø50 bolt circle, centred, from 45° | SUCCEEDED | 61989.380702 = 61989.380702 | 10×80×80 exact |
| disc Ø50 x 5, central Ø10 bore | SUCCEEDED | 9424.777961 = 9424.777961 | 5×50×50 exact |

STEP volume of curved solids is reported not_verifiable (no kernel), never passed. First attempt (kept in
`_out\text2cad\attempt1_through_all`) used through-all holes: volumes 0.4–2 ppm high, STEP thickness 5.000006
→ the disc run was correctly EXECUTED_UNVERIFIED; holes now use depth = thickness. The hole-row run also exposed
two `mac` defects, fixed: a stale mass read (volume 0, density NaN) crashed the JSON report and left the part
open in SOLIDWORKS; non-finite values are now written as null and a zero-volume read of a solid is re-read once
after a forced rebuild (noted in the report; that retry did not trigger in the re-runs, so it is untested live).

**Editing existing parts** (`astracad_cad edit` → `mac edit`, which edits a copy):

| Edit | Task | Original file | Kernel read-back | Independent STEP re-read |
|---|---|---|---|---|
| block D1@Boss-Extrude1 20 → 35 mm | SUCCEEDED | SHA-256 unchanged | 35 mm; V = 84000.000000 mm³ | 60×40×35; V = 84000.000000 |
| shaft D1@Boss-Extrude1 100 → 120 mm | SUCCEEDED | unchanged | 120 mm | 25×25×120 exact |
| unknown name D9@NoSuchFeature | FAILED | unchanged | — | copy discarded, no file left |
| shaft **D1@Sketch1 Ø25 → 30 mm** (sketch dim, picked from a "diameter" proposal) | SUCCEEDED | unchanged | 30 mm | 30×30×100 exact |

**New features** (`astracad_cad spec <json> --authorize KM` → orchestrator → `mac run`):

| Spec | Task | Kernel vs independent expectation | Independent STEP re-read |
|---|---|---|---|
| stepped shaft, revolved profile Ø35×40 + Ø25×20 | SUCCEEDED | V 48301.987049 = Pappus 48301.987049 (Δ 0); A Δ 2.7e-12 | 35×35×60 exact; volume not_verifiable (curved) |
| 100×40×10 plate, Ø6 hole × linear pattern 4 @ 20 mm | SUCCEEDED | V 38869.026645 = seed×4 38869.026645 (Δ 0); A Δ 3.6e-12 | 10×40×100 exact |

**Live 2026-10-18** (`astracad_cad build "<request>" --authorize KM`, output `_out/verify_20261018/`), verifying the circular
pattern and pocket / slot work committed while Claude was unavailable:

| Request | Task | Kernel vs prediction | Independent STEP |
|---|---|---|---|
| 80×80×10 plate, 4×Ø8 on PCD 50, centred, from 45° (seed + `CirPattern1` about an axis at (40,40)) | SUCCEEDED | V 61989.380702 = 61989.380702; A 16603.185789 = 16603.185789; CoM (40,40,5) | 10×80×80 exact; hole centres 22.3223 / 57.6777 = 40 ± 25·cos45° |
| flange disc Ø100×12, 6×Ø10 on PCD 70 from 30° (axis through origin) | SUCCEEDED | V 88592.912831 =; A 20797.343367 = | 6 hole centres = (35 cos(30+60k), 35 sin(30+60k)) to 4 dp |
| 60×40×20 block, 30×20 pocket 5 deep, centred | SUCCEEDED | V 45000 =; A 9300 =; CoM (30,20,10.5) | size exact |
| 80×50×15 block, 40×10 slot 6 deep along the width, centred | SUCCEEDED | V 57600 =; A 12500 = | size exact |
| 60×40×10 block, 20×10 pocket through, centred | SUCCEEDED | V 22000 =; A 7000 = | size exact |

**Drawings from parts** (`astracad_cad drawing <part> --name N --out DIR --authorize KM` -> `mac drawing`):

| Part | Task | mac (model dims on drawing) | Independent: astracad_inspect on the exported DXF |
|---|---|---|---|
| holed block 60x40x20 Ø10 (1st and 3rd angle) | SUCCEEDED x2 | 4/4 equal | unit mm; 4/4 geometry cross-checks; printed 60.00/40.00/20.00/⌀10.00 1:1 with model; none extra |
| shaft Ø25x100 | SUCCEEDED | 2/2 equal | 2/2 cross-checks; ⌀25.00 and 100.00 1:1 |

Three runs back to back took 8 s each. Part SHA-256 unchanged every time. SOLIDWORKS here is an
**Educational** licence: every sheet carries "SOLIDWORKS Educational Product. For Instructional Use Only."

Status probe observed NOT_CONNECTED (SW closed) and CONNECTED (SW open). Creo, Fusion, AutoCAD: NOT_INSTALLED on this PC.

**Word → dimension proposal** (live, 2026-10-17): `build "change the length of \"…shaft_d25_l100.SLDPRT\" to 120 mm"` →
read-only inspect task, 1 candidate `D1@Boss-Extrude1` (100 mm, hint only), `auto_selected: false`, exit 3,
source SHA-256 unchanged, no output written. Nothing is ever selected for the user.
`mac` now dimensions what it sketches (rectangle W and H, circle and hole Ø) and `mac inspect` walks
sub-features, so sketch dimensions are listed: shaft → D1@Boss-Extrude1 100, D1@Sketch1 25; holed block →
60, 40, Ø10, 20. Rebuilt volumes identical to the earlier runs (49087.385212 / 46429.288889 mm³).
Angular dimensions are skipped (radians are never reported as mm).
Each dimension now carries its kernel type (`IDisplayDimension.Type2`). Live "diameter" proposals:
shaft → D1@Sketch1 25 diameter **match**, D1@Boss-Extrude1 linear mismatch; holed block → D1@Sketch2 Ø10
**match**, three linear mismatches listed after it. A diameter requested on a radius dimension is halved (CALCULATED).

## Defects found by the live runs and fixed (each with a regression test)

1. Adapter accepted `not_verifiable`, task store rejected it → exception left a task RUNNING. Policy unified (no failed + ≥1 passed); orchestrator can no longer leave a task RUNNING.
2. SOLIDWORKS writes circles as two arcs → bounding box was flagged approximate. Exact arc extents now.
3. Holed and plain blocks shared a file name → second run overwrote the first. Distinct names; adapter refuses to overwrite.
4. A failed edit (unknown dimension) still saved and exported an unedited copy that looked like a result. `mac` now discards it; the adapter reports FAILED whenever any requested dimension was not set.
5. `mac inspect` reported a pattern's instance count as a length (`D1@LPattern1` = "4000 mm"). Only length-type dimensions are reported now; the pattern spacing (20 mm) still is.
6. `CreateLine` does not open a sketch the way rectangle/circle creation does (0 lines made) — the profile path opens it explicitly.
7. The default drawing template is in inches: the first generated drawing printed 2.36 for a 60 mm edge and put
   three dimensions in two views each. `mac drawing` now forces MMGS and removes duplicates; that real inch DXF is
   kept as a fixture that the independent check must (and does) reject although mac's own report said ok.
8. **The DXF reader ignored DIMLFAC** (a dimension style's scale factor), so every drawing exported at a scale other
   than 1:1 failed its geometry cross-check (a 2:1 SOLIDWORKS view has DIMLFAC 0.5). Applied now, with the unscaled
   reading kept as a named route. The three synthetic fixtures carried ezdxf's default DIMLFAC 100 that contradicted
   their own printed values; set to 1 in the files and in the generators.
9. Rows (what the PDF/XLSX print) were built before late checks added warnings, so `possible_duplicate_callout`
   never reached the exports. Rows are now refreshed; the duplicate key also includes the tolerance (flatness 0.02
   and parallelism 0.02 | A were wrongly flagged as duplicates).
10. A `mac` timeout escaped the adapter as an exception. It is now a FAILED result with recovery advice.
Earlier this session: pipeline crashed when PyMuPDF was absent → pluggable reader. pypdfium2 is now the declared reader; PyMuPDF is an optional extra.

## Known limitations (top)

- Real-drawing accuracy measured on 18 SOLIDWORKS sample sheets only (vendor samples, one CAD system);
  no customer drawings yet. SOLIDWORKS cannot export DXF from assembly drawings here (error 1): PDF only.
- Scans: OCR recall is low on cluttered text (3/9 on the synthetic scan — circles/lines crossing text, stacked
  tolerances, vertical text); precision is the gate (both engines must agree). Windows-only (Windows.Media.Ocr).
- GD&T: frames read from DXF TOLERANCE entities and Unicode text; MMC/LMC bonus not computed; datum feature
  symbols not linked; frames drawn as graphics in PDFs are missed. No title block from PDF/scans.
- SOLIDWORKS: existing parts can have named dimensions changed (on a copy) but not new features;
  dimension names must be given to edit; a word ("diameter") only yields a candidate list, and
  `mac inspect` reports each dimension's kernel type (diameter/radius/linear; angular skipped), which
  orders a word's candidates but never selects one; parts `mac` built before this change have undimensioned sketches;
  revolve and linear pattern exist for new parts (revolve profile sketches carry no driving dimensions yet);
  no shell/mirror/circular pattern, assemblies or drawings (.SLDDRW).
- Drawings: three standard views + model dimensions only; no title block/sheet format, no tolerances added,
  parts only. One run hung inside SOLIDWORKS (Model Items prompt left open) right after a run in the same session;
  not reproduced in 4 later runs after a restart; root cause unproven.
- Creo/Fusion/AutoCAD: not installed here, so no automation exists — discovery only.
- Local web workspace is read-mostly; CAD creation/editing stays CLI-only. Costing is a stated block-machining
  model (no fixturing/tool wear/finishing/inspection; no real prices shipped). No CAM, no simulation.
- No parity or superiority vs MecAgent/Pre6 is demonstrated (MecAgent not run; Lens run only on its own sample, no shared labelled drawings). Pre6 Lens's own manual
  lists PDF/JPG/PNG/TIFF input (no DXF) and OCR; its public example (observed, P6-23…31) ballooned 73 dims + GD&T on a raster in ~10 s.
Full list: `docs/engineering/known-limitations.md`.
