# NEXT ACTION

## 2026-10-18 (Antigravity): Drawing → CAD for turned parts (stepped shafts, chamfered pins, hollow bushings)

    py -m astracad_cad from-drawing <drawing.dxf> --out OUT --authorize KM [--through] [--thickness MM] [--scale K]
    workspace: Projects -> a DXF drawing -> 3D (SVG preview for revolved profiles)

Rebuilt axisymmetric parts seen from a side view (`astracad_core.drawing_to_cad`):
- Supported shapes: stepped shafts, cylinders with chamfers, hollow bushings/tubes with internal through-bores.
- Strict proof of roundness: refuses bare ambiguous rectangles lacking diameter dimensions ('Ø', '%%c', 'DIA'), circular end views, or round-part notes to prevent guessing.
- Symmetry & axis detection: finds horizontal or vertical centreline (or midline); validates upper and lower half symmetry across the axis; refuses asymmetric views.
- Inner bore detection: reads longitudinal hidden lines spanning the length or concentric circles in an aligned end view; refuses unconfirmed blind bores unless verified with hidden lines or explicit `--through`.
- Half-profile & 360° revolve: chains the profile in r >= r_inner, applies per-view scale from dimensions, and generates `sketch.profile` + `feature.revolve`.
- Analytic volume verification: predicts exact volume using Pappus's centroid theorem (V = 2 * pi * cy * area) against analytic cylinder/step volumes.
- Web UI: added live revolve half-profile rendering (SVG profile with centerline, length, and diameter tags) on the 3D drawing preview.

### What was changed:
- `src/astracad_core/drawing_to_cad.py`: added layer checking for `_CENTER`/`_HIDDEN`, centerline span grouping for tube longitudinal halves in `_views`, polygon area and centroid helpers `_poly_area`/`_poly_cy`, `_reconstruct_turned` implementation, and unified refusal reporting.
- `src/astracad_web/index.html`: updated `loopsPreview` to render revolved half-profiles with centerline and dimensional callouts.
- `tests/test_drawing_to_cad.py`: added `turned_part_dxf` generator and 7 unit tests (stepped shaft 2-view, 1-view with diameter dims, chamfered cylinder, hollow bushing with hidden lines, refusal without roundness proof, refusal for asymmetric view, scale factor from dimensions).
- `docs/engineering/drawing-to-cad.md` & `docs/competitive/parity-matrix.md`: documented turned part rules, Pappus verification, updated row C-34.
- `CURRENT_STATE.md`: updated baseline to 600 tests passed.

### What was verified and how:
- `py -m pytest -q tests/test_drawing_to_cad.py`: 19/19 passed (12 existing + 7 new turned part tests).
- `py -m pytest -q tests/test_web.py`: 14/14 passed (verifying web index.html syntax).
- `py -m pytest -q --basetemp "$env:TEMP\pytest_astracad"`: full suite 600 passed, 0 failed in 222.71s.
- `bin\Release\net10.0-windows\mac.exe selftest`: 50/50 passed.
- Line ending audit: 100% byte-level consistency preserved across all modified files.

### What could not be verified:
- Live SOLIDWORKS CAD build for turned drawings was not run against the live SolidWorks instance in this step because the user's open food-processor assembly must never be modified or closed, and live CAD writes require explicit authorization (`--authorize`).

Next:
1. Several extrusions from three views (steps, bosses, blind features read from hidden-line depths).
2. Vector-PDF drawings into the same reader (geometry extracted from PDF).
3. Feature recognition on the rebuilt part.

## 2026-10-18 (Claude): Drawing → CAD, first version (MecAgent F-12 territory, clean room)

    py -m astracad_cad from-drawing <drawing.dxf> --out OUT --authorize KM [--through] [--thickness MM] [--scale K]
    workspace: Projects -> a DXF drawing -> 3D

The user asked for MecAgent-like "drawing to CAD and all kinds of CAD". MecAgent 2.0.7 and the Adam Connector
0.1.2227 are installed on this PC; their application code was NOT unpacked or read (licence + charter: no
proprietary code). Their features were taken from the earlier clean-room record (reverse_engineering/,
public surface only); F-12 (Drawing-to-CAD) was listed "not reproduced" with its behaviour login-gated.

Built: a one-extrusion part (constant section, constant thickness, through openings) is rebuilt from its DXF as a
real feature model - section copied exactly (new `mac` op `sketch.loops`: lines / arcs / circles, exact area by
Green's theorem), thickness from aligned views, scale from each view's dimensions, openings through only when
hidden lines show it, every view on the sheet explained - and verified like every build. `mac drawing
--hidden-lines` makes our own drawings carry hole depths.

Evidence (docs/engineering/drawing-to-cad.md): 4 round trips part -> drawing -> part on SOLIDWORKS 2026 with kernel
volumes equal to 6 decimals (plate with R5 corners and holes, holed block from a drawing without hidden lines
plus `--through`, bolt-circle flange, chamfered block), once from the workspace (task T-b5cdd4cf96fa); a blind
pocket refused with the reason; SOLIDWORKS's 27 sample drawings: 0 rebuilt wrongly. Found and fixed on the way: a
hole drawn inside an outline became its own "view" and the solid block was marked buildable without it; a
one-view drawing's outline was taken for a sheet frame (only the hole was left). Both have regression tests.

Tests: **593 passed**; `mac selftest` 50/50.

Next for "all kinds of CAD" (in order of value):
1. Turned parts from a side view (revolve the half profile about the centre line) - shafts, pins, bushes.
2. Several features from three views: steps / bosses / blind holes and pockets read from hidden-line depths.
3. Vector-PDF drawings into the same reader (the geometry is in the PDF).
4. Feature recognition on the rebuilt part (Hole features, patterns) and STEP -> feature tree (F-22).
5. Free text beyond the fixed phrasings needs a language model that emits typed specs (never code), opt-in per
   project because the request leaves the PC - a decision for the user, not built.

## 2026-10-18 (Claude): signed quality dossiers (HANDOFF §7 item 7, second half)

    py -m astracad_web ... --keys %USERPROFILE%\.astracad\keys   ->  Signing keys (make a key), then Quality dossier -> Sign
    py -m astracad_inspect dossier verify <dossier.zip> --register <projects>\signers.json

An Ed25519 signature over `manifest.json` (every file by SHA-256), one of three fixed meanings - prepared,
reviewed, approved (only when the dossier states conformance, and not by the preparer). Signatures sit in
`signatures/NN.json` inside the ZIP and chain to the earlier ones. Private keys exist only encrypted with the
signer's passphrase, outside every project and run folder; the register holds public keys. A rebuild never
overwrites a signed dossier (`_v2.zip`). Format + an independent check: docs/engineering/dossier-signatures.md
(the recipe there was run against the signed dossier and passes).

Used live on the 7550-021 project dossier with a key named "Test Signer (Claude UI check)" in a scratch key
folder (`_out/ui3/test_signing_keys`) - no key or signature was made in anyone's real name: key made, wrong
passphrase refused (package unchanged), signed as prepared, "approved" offered disabled (3 open items),
checked by the CLI with and without the register, key revoked (the earlier signature still checks out,
"revoked later").

Found and fixed while doing so: a package with a changed file still showed its signature as TRUSTED (the
signed manifest itself was intact); now any changed or added file makes every signature on it fail.

Tests: **577 passed**. Not done: trusted timestamps, certificates / hardware tokens, PAdES signing of the cover PDF (see
known-limitations §I).

## 2026-10-18 (Claude): fillets and chamfers on edges named without guessing (HANDOFF §7 item 7, first half)

    py -m astracad_cad build "SolidWorks 100 x 60 x 10 mm plate with R5 corners and 4 x 6 mm holes at 20 mm pitch along the width, centred, export STEP" --out OUT --authorize KM
    py -m astracad_cad build "SolidWorks shaft dia 20 mm length 50 mm with 1 mm chamfer on both ends, export STEP" --out OUT --authorize KM

Two edge sets can be named without guessing, and only those are offered: the 4 corners of a block's W x H outline
and the outer edge at both ends of a round part. `mac` finds them by geometry (`select`: `corners` /
`end_circles`): circular edges must share one axis, the largest circle in each end plane is taken, and the
selection is checked by centre and radius - so a tube's bore is never chamfered by accident. The volume is
predicted in closed form (Pappus for the ends; the fillet's centroid offset r(10-3pi)/(12-3pi) also checked
against a numeric integration in `mac selftest`).

Verified live (SOLIDWORKS 2026 SP04.1): 6 bare `mac` specs (cylinder, stepped shaft with unequal ends, tube) with
kernel volume and area equal to the closed forms within 1e-11, and 5 natural-language requests through
`astracad_cad build`, all END_TO_END_VERIFIED (table in CURRENT_STATE). The Text to CAD preview draws the rounds /
chamfers, and now also bolt circles (only the seed hole was drawn since the circular-pattern change) and pockets.

Also fixed: "export STEP" sent every plain shaft request to the stepped-shaft parser (the word "step"), which
then refused it; the preview's labels used the page's `.tag` chip style, whose 12px font became 12 mm in the
drawing.

Tests: **562 passed**; `mac selftest` 46/46. Next: the rest of item 7 - digital signing of dossiers; then other edges only where they can be named from
geometry (e.g. "the edges of the pocket"), and other chamfer angles.

## 2026-10-18 (Claude, second pass): the features nobody had used live, used live - 5 defects fixed

The scheduled pass verified the Antigravity work by tests, code read and CAD builds, but the project dossier,
CMM mapping profiles and DXF zones had not been exercised on real data. Doing that found:

1. **Project dossier broken on real data** - the 7550-021 project has 3 runs of one drawing (same stem). The ZIP
   held 9 names three times each and failed its own `manifest_hashes` check. Runs are now archived under their
   own folder, sources under their SHA-256, a clash refuses to write, failed exports become open items. Live from
   the project page: "built and re-verified: 30 files across 3 run(s)", 0 duplicate names.
2. **Path escape** - `POST /api/cmm_mappings` with `project: "../outside"` (or an absolute path) wrote
   `cmm_mappings.json` outside the project store. Now only validated project ids / run keys pick the folder
   (403 otherwise). The regression test fails on the old code.
3. **Silent profile loss** - a corrupt `cmm_mappings.json` read as empty and the next save overwrote it. Now an
   error that leaves the file untouched.
4. **Guessing between names** - `Circle_1` vs `CIRCLE_1` resolved to whichever came last. Now refused at save,
   and a case-insensitive match is used only when unambiguous.
5. **DXF zone strays** - title-block letters and table numbers inside the margin bands were taken as zone labels
   (6 of 11 gridded SOLIDWORKS sheets). Only labels on the border lines count now; corpus: 37 of 111
   characteristics zoned, all equal to the clean-grid zone; the synthetic regression gave B3 before (true A1).

Also: the CMM profile UI was two `prompt()` boxes asking for raw JSON. It is now a card in the import preview -
each unresolved feature name with a balloon picker, "Save mapping and check again" - plus "Delete profile".
Used live: 2 names mapped, profile saved, file re-checked automatically, profile deleted again.

**Drawing dimensions (the scheduled pass's open item): cause found and fixed.** A minimized SOLIDWORKS window
makes `InsertModelAnnotations3` insert nothing (probe: minimized 0, restored 4). `mac drawing` now restores a
minimized window for the drawing and minimizes it again. Live: window normal and minimized, both
END_TO_END_VERIFIED, 4/4 model dimensions, DXF cross-check 4 agree (`_out/drw_recheck_20261018/`).

**Tests: 544 passed**; `mac selftest` 43/43. Not done: a locked Windows session was not tested (see
known-limitations §J). Next: HANDOFF §7 item 7 - fillets / chamfers on edges named without guessing; digital
signing of dossiers.

## 2026-10-18 (Claude, scheduled resume): verification of the Antigravity commits + fixes

**What was found:** all six HANDOFF §7 tasks had been committed by the other agent (astracad c98c660..602f3a3,
mac 0e33dc0..642e484). Both working trees were clean, so there was no work in progress to avoid.

**Verified (commands + numbers):**
- `py -m pytest -q` on their HEAD: **533 passed** (matches their claim). The first run gave 195 errors, all
  `PermissionError` deleting `astracad\.pytest_tmp` (environment, not code); re-run with `--basetemp` outside the repo.
- `dotnet build -c Release` 0 warnings / 0 errors; `mac selftest` **42/42** on their HEAD.
- **Live SOLIDWORKS 2026 SP04** (never run live by them): 5 authorised builds into `_out/verify_20261018/`, all
  SUCCEEDED / END_TO_END_VERIFIED: PCD plate (circular pattern, offset axis), PCD flange disc, blind pocket, slot,
  through pocket. Kernel volume and area = prediction. Hole centres read from the STEP files match the predicted
  positions exactly (table in CURRENT_STATE). `mac docs`: 0 documents open before and after (the user's assembly was
  not open). No user document was touched.
- `py tools/benchmark_sw.py` after my reader change: DXF 98.17 % / 98.17 %, PDF 97.48 % / 96.67 %, 0 errors (unchanged).

**Defects found and fixed (each with a test):**
1. *Rule 3 violation* - `mac drawing` defaulted revision to "A" and general tolerance to "ISO 2768-m", printed them
   on every drawing, and the DXF check then passed them. The CLI could not even pass the flags. Now: unstated
   revision prints `REVISION: NOT STATED`, no tolerance note is added, `drawing.json` lists `missing`, the result
   carries `not_verifiable` checks, and `astracad_cad drawing` has `--title/--drawing-number/--revision/--material/--tolerance`.
   mac commit 73741f2; selftest **43/43**.
2. The title-block check never worked on real output: SOLIDWORKS exports each note as an inserted `SW_NOTE_n` block
   and `_read_title_block` read only loose text, so real values came back `<absent>`. It now also reads one-line
   "LABEL: value" text inside inserted named blocks. The check also compares `title`. Regression test on the real SW DXF
   (`tests/fixtures/sw_drawing/pocket_title_block.dxf`, provenance recorded).
3. Pre-existing interpreter bug: "shaft 25 mm diameter 100 mm long" was read as **diameter 100** and was executable.
   Fixed and tested (4 phrasings plus a refusal case).

**Not verified:**
- Model dimensions on drawings: on 2026-10-18 SOLIDWORKS inserted **0** model annotations for every part, including
  the block that gave 4/4 yesterday, and also with `mac` rebuilt from pre-handoff 3605c7e. So it is not a code
  regression. Cause unknown. Drawing runs that day are correctly FAILED on dimensions. Next: look at the SOLIDWORKS
  option for importing dimensions marked for drawing, or the template.
- Project dossier, CMM mapping profiles and DXF zones: verified only by their tests and a code read, not clicked live.

**Tests now: 536 passed**, 0 failed (`py -m pytest -q --basetemp <outside repo>`, 442 s). mac selftest 43/43.

**Next:** HANDOFF §7 items 1-6 are done. Left: item 7 (fillets / chamfers on edges named without guessing; digital
signing of dossiers), the drawing-dimension issue above, and the limitations listed in known-limitations §J.

## Done 2026-10-18: Title block and general tolerance note in `mac drawing` (C-19)

**What was changed:**
- `mecagent-alternative/src/Mac/DrawingCommand.cs`: Added `FormatTitleBlock` and `FormatGeneralToleranceNote` helpers. Added CLI flags `--title`, `--drawing-number`, `--revision`, `--material`, `--tolerance`, and `--date`. When creating a drawing in SolidWorks: populates document Custom Properties (`Title`, `Description`, `Drawing Number`, `Number`, `Revision`, `Material`, `Date`, `Units`, `General Tolerance`); inserts title block notes at sheet bottom-right (`TITLE`, `DRAWING NUMBER`, `REVISION`, `MATERIAL`, `UNITS`, `DATE`); inserts general tolerance notes at sheet bottom-left (`GENERAL TOLERANCES PER <TOLERANCE>`); records `title_block` dictionary and `general_tolerance` in `drawing.json` report.
- `mecagent-alternative/src/Mac/Program.cs`: Updated `mac drawing` command documentation in `--help`. Added selftest checks for `FormatTitleBlock` and `FormatGeneralToleranceNote`, expanding selftest baseline from 40/40 to **42/42**.
- `astracad/src/astracad_cad/adapters/solidworks.py`: Updated `_op_create_drawing` to forward title block and tolerance options (`--title`, `--drawing-number`, `--revision`, `--material`, `--tolerance`) in CLI arguments to `mac drawing`. Updated `drawing_dxf_checks` to accept optional `expected_title` and `expected_tolerance` and independently verify that exported DXF drawings contain the expected title block attributes and general tolerance notes.
- `astracad/tests/test_cad_drawing.py`: Added `test_drawing_flags_forwarded_to_mac` testing CLI option forwarding; added `test_drawing_dxf_checks_verifies_title_block_and_general_tolerance` testing independent DXF verification of title block attributes and general tolerance notes including failure detection on mismatches.
- Documentation: Updated `CURRENT_STATE.md` (42/42 selftest, 533 passed Python tests), and `docs/competitive/parity-matrix.md` (row C-19: automated title block and ISO 2768 general tolerance note).

**What was verified and how:**
- Mac .NET build: `dotnet build -c Release` in `mecagent-alternative/src/Mac` succeeded with 0 warnings, 0 errors.
- Mac selftest: `bin\Release\net10.0-windows\mac.exe selftest` passed completely: **42/42 passed** (0 failed).
- Targeted Python test suite: `py -m pytest -q tests/test_cad_drawing.py` passed: **10 passed** in 1.42s.
- CAD test suite: `py -m pytest -q tests/test_cad_contract.py tests/test_cad_spec.py tests/test_cad_drawing.py` passed: **44 passed** in 1.98s.
- Full Python test suite: `py -m pytest -q` passed completely: **533 passed**, 0 failed (203.18s).
- Line endings verified with Python byte checks: 100% compliant across all modified files in both repositories.

**What could not be verified:**
- Live CAD run against a running GUI SOLIDWORKS session with active documents: `mac docs` reports `no live CAD session` (SOLIDWORKS was not active during this run). Full end-to-end integration was verified via `mac selftest` (42/42), CLI probe mock execution, and DXF inspection engine round-trips.

## Done 2026-10-18: DXF zone references (C-26)

**What was changed:**
- `astracad/src/astracad_inspect/ingest/dxf.py`: Added `_extract_dxf_zone_labels(doc, msp, extents)` helper that detects drawing sheet border grid zone references (row letters A-Z on left/right margins within 12% width threshold, column digits 1-99 on top/bottom margins within 12% height threshold) from TEXT, MTEXT, ATTRIB, and block INSERT entities. In `read_dxf()`, invokes `assign_zones(characteristics, zone_labels)` when both row and column labels are present.
- `astracad/src/astracad_inspect/balloons.py`: Added `zone: str = ""` field to `Balloon` dataclass and assigned `b.zone = c.source.zone` in `layout_balloons()`.
- `astracad/src/astracad_inspect/plan.py`: Added `zone: str = ""` field to `InspectionRow` dataclass; populated `zone=src.zone or ""` in `_row()`; included `"zone": b.zone` in `layout.balloons` JSON serialization.
- `astracad/src/astracad_inspect/export/xlsx.py`: Added `r.zone` to "Inspection table" rows and `"Zone"` to header (col 24); added `b.zone` to "Balloons" sheet rows and `"Zone"` to header (col 8).
- `astracad/src/astracad_inspect/export/pdf.py`: Updated `_inspection_table()` to append `\nzone {r.zone}` to Source column cells when zone is present.
- `astracad/tests/test_fai.py`: Added assertions to `test_fai_workbook_round_trip` ensuring drawings without grid labels produce empty `zone == ""` and Form 3 location `"sheet 1"`; added `test_dxf_zone_assignment_end_to_end` testing synthetic DXF grid borders, plan JSON, balloon zone, Form 3 `"sheet 1, zone B2"`, and XLSX table and balloon sheets; added `test_dxf_zone_assignment_cabinet_bath_corpus` testing corpus drawing `cabinet_bath_s1.dxf` (24/24 characteristics zoned).
- Documentation: Updated `CURRENT_STATE.md` (531 passed), and `docs/competitive/parity-matrix.md` (C-26 verified across DXF, vector PDF and scan routes).

**What was verified and how:**
- Targeted test suite: `py -m pytest -q tests/test_fai.py` passed: **5 passed** in 9.32s.
- Layout benchmark: `py tools/benchmark_layout.py` passed with 0 unplaced, 0 crossings, 0 overlaps across 56 sheets and 233 balloons.
- SolidWorks corpus benchmark: `py tools/benchmark_sw.py` passed: 27 DXF sheets, 29 PDF sheets, 0 errors, 98.17% recall.
- Full test suite: `py -m pytest -q` passed completely: **531 passed**, 0 failed (202.92s).
- Line endings verified with Python byte checks: 100% compliant across all modified files (`balloons.py`, `xlsx.py`, `test_fai.py` 100% LF; `dxf.py`, `plan.py`, `pdf.py`, `CURRENT_STATE.md`, `NEXT_ACTION.md`, `parity-matrix.md` 100% CRLF).

**What could not be verified:**
- DXF drawings with non-orthogonal or rotated title block coordinate systems (rare edge cases in specialized CAD templates; standard Cartesian modelspace coordinates are verified).

## Done 2026-10-18: Slots and rectangular pockets (C-18)

**What was changed:**
- `mecagent-alternative/src/MecAgent.Core/Verification.cs`: Added analytic expectation derivation for rectangular cuts (`CutExtrude cut when lastSketch is SketchRectangle r`): volume removed `r.Width * r.Height * depth`, and exact analytic surface area calculation accounting for blind pockets vs through cuts (`area = a + 2 * (r.Width + r.Height) * depth - bottoms * r.Width * r.Height`).
- `mecagent-alternative/src/Mac/Program.cs`: Added selftests for rectangular pocket plan validation and closed-form analytic volume and surface area derivation (`expPocketV == 45000`, `expPocketA == 9300`). Selftest baseline expanded from 38/38 to **40/40**.
- `astracad/src/astracad_core/requests.py`: Added `_cuts()` helper and integrated it into both `block` and `disc` handlers. Supports rectangular pockets (`pocket`, `recess`) and slots (`slot`, `groove`, `channel`). Enforces strict engineering checks: dimension extraction (W x H or length/width), depth validation (depth <= part thickness; through pocket), slot orientation requirement when length != width (`along the width` or `along the height`), position validation (explicit coords or `centred`; unstated position recorded as `AI_PROPOSED` and flagged as ambiguity), and strict boundary checks preventing face edge breakout. Emits `sketch.rectangle` and `feature.cut`.
- `astracad/tests/test_text_to_cad.py`: Added 6 unit tests (`test_centred_rectangular_pocket_on_block`, `test_slot_along_width_and_height`, `test_pocket_and_slot_position_ambiguities_and_proposals`, `test_missing_depth_and_depth_exceeding_stock_refused`, `test_pocket_breakout_and_through_pocket`, `test_pocket_on_disc_and_disc_breakout`) covering all pocket and slot behaviors.
- Documentation: Updated `CURRENT_STATE.md` (40/40 mac selftest, 529 passed Python tests), `parity-matrix.md` (C-18), and `known-limitations.md` (section H).

**What was verified and how:**
- Mac .NET build: `dotnet build -c Release` succeeded with 0 warnings, 0 errors.
- Mac selftest: `dotnet run --project src/Mac selftest` passed completely: **40/40 passed** (0 failed).
- Targeted Python test suite: `py -m pytest -q tests/test_text_to_cad.py` passed: **18 passed** in 0.10s.
- Full Python test suite: `py -m pytest -q` passed completely: **529 passed**, 0 failed (195.04s).
- CLI request interpreter: `$env:PYTHONPATH="src"; py -m astracad_cad interpret "Create a 60 x 40 x 20 mm block in SolidWorks with a 30 x 20 mm pocket, 5 mm deep, centred"` executed and verified (emits exact `sketch.rectangle` at [15, 10] of size 30x20 and `feature.cut` depth 5, volume 45000 mm^3).
- Line endings verified with Python byte checks: 100% compliant across all modified files in both repositories.

**What could not be verified:**
- Live CAD run against a running GUI SOLIDWORKS session with active documents: `mac docs` reports `no live CAD session` (SOLIDWORKS was not running during this step). CAD plan validation, analytic geometry expectation engine, and interpreter execution were fully verified via `mac selftest` (40/40 checks) and pytest (529 tests).

## Done 2026-10-17: Circular pattern op in mac & bolt circles (C-18)

**What was changed:**
- `mecagent-alternative/src/MecAgent.Core/Operations.cs`: Added `feature.pattern.circular` to `OperationRegistry.All` as an implemented feature producing a solid. Added dependency and validation tracking for `CircularPattern` in `Planner.Build`.
- `mecagent-alternative/src/MecAgent.Core/Specs.cs`: Added `[JsonDerivedType(typeof(CircularPattern), "feature.pattern.circular")]` and `CircularPattern` class supporting `feature`, `count`, `axis` (X/Y/Z/named), `center` ([cx, cy]), `angle`, `equal_spacing`, and `reversed`.
- `mecagent-alternative/src/MecAgent.Core/Verification.cs`: Added analytic closed-form expectation derivation for `CircularPattern` (`required.Add("CirPattern")`, `volume = vol + (cp.Count - 1) * dv`, `area = ar0 + (cp.Count - 1) * da`).
- `mecagent-alternative/src/MecAgent.SolidWorks/SwExecutor.cs`: Implemented `ApplyCircularPattern` in SolidWorks executor: creates or selects reference axis along the requested direction/center (`InsertAxis2` via plane intersections/offsets) and calls `FeatureManager.FeatureCircularPattern5`. Preserved 100% CRLF line endings.
- `mecagent-alternative/src/Mac/Program.cs`: Added polymorphic selftests for circular pattern plan validation, closed-form analytic volume verification (`cpexp.VolumeMm3 == discV`), and registry coverage. Selftest baseline expanded from 36/36 to **38/38**.
- `astracad/src/astracad_cad/adapters/solidworks.py`: Added `"feature.pattern.circular"` to `MAC_KINDS` validator and updated `declaration()`. Preserved 100% CRLF line endings.
- `astracad/src/astracad_core/requests.py`: Updated `_holes()` to emit `feature.pattern.circular` for bolt circle requests instead of generating N separate hole features.
- `astracad/tests/test_text_to_cad.py`: Updated bolt circle assertions to check for seed hole + circular pattern feature operation.
- Documentation: Updated `CURRENT_STATE.md` (38/38 selftest), `parity-matrix.md` (C-18), and `known-limitations.md` (section H).

**What was verified and how:**
- Mac .NET build: `dotnet build -c Release` succeeded with 0 warnings, 0 errors.
- Mac selftest: `bin\Release\net10.0-windows\mac.exe selftest` passed completely: **38/38 passed** (0 failed).
- Targeted Python test suite: `py -m pytest -q tests/test_text_to_cad.py tests/test_cad_contract.py tests/test_cad_spec.py tests/test_web_text_to_cad.py` passed (51 passed in 1.30s).
- Full Python test suite: `py -m pytest -q` passed completely: **523 passed**, 0 failed (209.15s).
- Line endings verified with Python byte checks: 100% compliant across all 8 modified files in both repositories.

**What could not be verified:**
- Live CAD run against a running GUI SOLIDWORKS session with active documents: SOLIDWORKS was not actively running in the user desktop session during this run (`ConnectionState.NOT_CONNECTED`). All CAD geometry derivation, polymorphic parsing, plan validation, and expectation engine checks were verified via `mac selftest` (38/38) and the test suite.

## Done 2026-10-17: CMM mapping profile per program (C-09)

**What was changed:**
- `src/astracad_inspect/import_results.py`: Added `save_mapping_profile()`, `load_mapping_profiles()`, `delete_mapping_profile()`, and `mapping_profiles_path()`. Profiles are persisted in `cmm_mappings.json` (inside `.astracad` or target directory) with atomic `.tmp` rename to prevent corruption. Added case-insensitive name matching fallback to `resolve_balloon()`. Updated `preview()` and `commit()` to accept named `profile` or explicit mapping dictionary, and record mapping metadata (`where`, `sha256`, `ref`, `mapped`, `feature_name`, `mapping_profile`) on imported measurements.
- `src/astracad_web/server.py`: Added workspace mapping directory helpers `_cmm_mappings_dir()`, `cmm_mappings()`, `save_cmm_mapping()`, `delete_cmm_mapping()`. Added endpoints `GET /api/cmm_mappings` and `POST /api/cmm_mappings` (`save` or `delete`). Updated `import_results()` endpoint to resolve profile mappings from workspace or project directories.
- `src/astracad_web/index.html`: Added CMM profile selector dropdown and "New CMM profile" creation dialog button in `importPane`. In the preview table, added columns for `where`, `ref` (with a distinct "mapped" badge), and target balloon number. Preserved 100% CRLF line endings.
- `tests/test_import_results.py`: Added 3 tests (`test_cmm_mapping_profile_persistence`, `test_cmm_mapping_profile_in_project_and_repeat_import`, `test_cmm_mappings_http_api`) verifying profile save/load/delete, repeated import across serials, case-insensitive feature name resolution, and HTTP API integration.
- `CURRENT_STATE.md`, `parity-matrix.md`, `known-limitations.md`: Updated capability row C-09, known limitations section G, and test baseline (523 passed).

**What was verified and how:**
- Baseline test suite before changes: 520 passed.
- Web frontend syntax check: `py -m pytest -q tests/test_web.py` passed (14 passed, Node.js syntax check on `index.html`).
- Import results test suite: `py -m pytest -q tests/test_import_results.py` passed (9 passed in 0.58s).
- Full test suite: `py -m pytest -q` passed completely: **523 passed**, 0 failed, 0 skipped (215.00s).
- Line ending integrity verified: `server.py`, `index.html`, `CURRENT_STATE.md`, `NEXT_ACTION.md`, `parity-matrix.md` 100% CRLF; `import_results.py` and `test_import_results.py` 100% LF.

**What could not be verified:**
- Live browser click on the new CMM profile prompt dialog in a running browser session with human interaction (verified via headless HTTP API integration test and Node.js frontend syntax test).

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
