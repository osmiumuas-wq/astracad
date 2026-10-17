# ASTraCAD — current state (evidence-based audit)

Audited: 2026-10-15, on this machine, by reading the files and running the commands listed
below. Nothing here is inferred from a README or from memory; every claim has an artefact or a
command behind it. Sections marked **ABSENT** are capabilities the constitution asks for that
do not exist yet — they are recorded as gaps, not described as "planned work in progress".

## 1. Workspace inventory

`C:\New folder (4)` contains three trees, none of which is a git repository:

| Tree | What it actually is | Evidence |
|---|---|---|
| `mecagent-alternative/` | .NET parametric CAD agent: `src/Mac` (CLI, `net10.0-windows`), `src/MecAgent.Core` (`net10.0`, spec/job/material/verification model), `src/MecAgent.SolidWorks` (`net10.0-windows`, COM executor/reader/session). References SolidWorks interop DLLs directly from `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\`. | `.csproj` files; `grep` of `TargetFramework`/`Reference` |
| `reverse_engineering/` | Output artefacts of that agent (`.SLDPRT`, `.step`, `.report.json` for prism/cylinder/hole/fillet/chamfer/steel specs) plus a Chromium `browser-profile/` directory. | `find -maxdepth 2` listing |
| `astracad/` | This project: `astracad_inspect`, a pure-Python inspection-layout engine built in this session. | tree below |

`dotnet --version` → `10.0.401`. The `mac` CLI and its SolidWorks interop are therefore usable
on this PC, but **ASTraCAD does not call them yet** — there is no adapter, no subprocess bridge,
and no shared contract beyond the JSON habit this project copied from `*.report.json`.

## 2. What `astracad/` is today

```
pyproject.toml                 package metadata + pinned deps + pytest config
requirements.lock              measured, pinned versions actually imported
requirements-dev.txt           test-only additions
src/astracad_inspect/
  model.py          186 LOC  characteristic/tolerance/issue/provenance-state types
  units.py           76      explicit unit handling, refuses undeclared sources
  callouts.py       285      deterministic callout parser (table-driven, no inference)
  limits.py         136      limit arithmetic, band width, inclusive evaluation, fit lookup
  iso286.py         155      ISO 286-2 table reader: resolves a class or refuses with a reason, and
                             states the loaded scope in one sentence both the plan and `doctor` use
  data/iso286-2.json         12262 lines of published deviation cells, keyed on ISO 286's nominal
                             size steps, plus the provenance and check record of each cell
  ingest/dxf.py     479      ezdxf reader: entity census, dimensions, text, provenance links
  balloons.py       165      lattice-slot balloon layout, non-overlap by construction
  plan.py           606      plan build, coverage, 10+ checks, release gate, overrides
  provenance.py     112      SHA-256, measured tool versions, run record, stamping
  export/render.py   94      DXF -> PNG via ezdxf matplotlib backend + ink measurement
  export/pdf.py     330      reportlab landscape A4 report
  export/xlsx.py    122      openpyxl workbook, 5 sheets
  validate.py       193      reopen-and-read verification of the exported artefacts
  pipeline.py        95      ordered end-to-end run
  cli.py            176      extract | run | doctor | selftest, documented exit codes
  selftest.py       145      34 checks that need no drawing
tests/              7 files + 2 labelled synthetic fixtures + their ground-truth JSON
tools/make_fixtures.py         generator for those fixtures and labels
tools/regression.py            metric harness: full route over every labelled fixture
tools/iso286_extract.py        builds data/iso286-2.json (729 LOC): places each published cell on
                               the size step its own colspan or printed position declares, keeps
                               only what two tables agree on, then bands it against the standard
                               Table 1
docs/                          this audit, target architecture, limitations, roadmap, licences
```

Total measured: 5575 lines of Python across package, tests and tools (excluding caches),
plus 5 documents in `docs/` and 2 lock/requirements files.

## 3. Verified behaviour right now

Measured on 2026-10-15 with `py -m pytest -q`, `py -m astracad_inspect selftest` and
`py tools/regression.py` in `astracad/`:

| Measurement | Result |
|---|---|
| pytest suite | **153 passed**, 0 failed (38 of them emit ezdxf's own NumPy-2.5 `shape` deprecation warning; the warnings come from ezdxf, not from this code) |
| self-contained checks | **34/34** selftest checks pass (no drawing needed) |
| Labelled extraction, `plate_synthetic` | recall 1.0, strict field accuracy 1.0, precision 1.0 over 10 labelled characteristics |
| Labelled extraction, `contradiction_synthetic` | recall 1.0, field accuracy 1.0 over 2 labelled characteristics |
| Reopen-and-read of exports | **19/19 checks pass per fixture (38/38)**: PDF re-opened with PyMuPDF, XLSX with openpyxl, JSON re-parsed |
| Deliberate callout/geometry contradiction | `90 ±0.1` on geometry drawn at 100 → `FAILED` and blocks; the neighbouring `20 ±0.1` → `PASSED` and does not block |
| Release gate | Both fixtures report **NOT releasable**, for named reasons: `plate_synthetic` blocks on CH-010 (`⌀10 H7/g6` — a fit pair names two features, so one characteristic resolves neither) and on the escalated free text `86.98 87.05` (`annotations_fully_interpreted`); `contradiction_synthetic` blocks on CH-001 (`geometry_cross_check_coverage`). `⌀20 H7` is no longer a blocker — it resolves to 20.000–20.021 from the loaded table. The engine does not claim completeness |
| Exit codes | 0 delivered+releasable, 3 delivered but not releasable (both fixtures return 3), 2 usage, 1 failure |

Fixture bytes pinned by SHA-256 at the time of writing:
`plate_synthetic.dxf 754aed31fb69…cb6f5bd8`,
`contradiction_synthetic.dxf e1bf29971c81…d9ce1c41`
(labels: `plate_synthetic.expected.json 4d164dfa5399…060655e7`,
`contradiction_synthetic.expected.json 28df0063d159…30b623c2`;
full values in `_out/regression_report.json`).

## 4. Decisions reused from `MecAgent.Core` (copied deliberately)

`src/MecAgent.Core/Verification.cs` already states the rule this project adopted:
*"Everything read back out of the CAD kernel. Produced only by measurement, never by the
executor's own claims, so a failed operation cannot certify itself as successful."* — and
`enum CheckOutcome { Passed, Failed, NotVerifiable }`.

`astracad_inspect.model` therefore uses the same three-valued outcome, keeps `expected` and
`actual` as separate measured strings, and carries a ten-state provenance model
(`user_specified / extracted / parsed / calculated / ai_proposed / verified / human_approved /
unresolved / rejected / superseded`) instead of a confidence score. The two runtimes stay
separate; JSON is the contract between them.

## 5. Environment facts that constrain the design

- Python `3.14.7` at `C:\Users\solid\AppData\Local\Python\pythoncore-3.14-64\python.exe`
  (invoked as `py`; `PYTHONIOENCODING=utf-8` is required for ⌀/°/± output on this console).
- Measured library versions: see `requirements.lock` — pinned to what is actually importable
  here, not to what a tutorial recommended.
- **`%TEMP%\pytest-of-solid` is not accessible** (`WinError 5`, even to `icacls`), while generic
  temp creation works. pytest is therefore rooted at `--basetemp=.pytest_tmp` inside the project
  (set in `pyproject.toml`) so test runs are reproducible on this PC.
- SolidWorks interop DLLs are present under `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\`, and
  per project memory: two properties on this install silently return wrong values, and CAD
  artefact hashes differ run to run. Any future SolidWorks adapter must re-read rather than trust.
- **No git repository** exists in this workspace. Provenance is therefore recorded by SHA-256 in
  the run artefacts; there is no commit-level history. `git init` has not been run because it was
  not authorised.

## 6. ABSENT — capabilities the constitution requires that do not exist yet

| Capability | State | What the engine does instead |
|---|---|---|
| PDF / raster drawing ingestion | ABSENT | refuses; only `.dxf` vector input is read |
| OCR of drawing text | ABSENT | no image is ever interpreted |
| GD&T feature control frames, datums | ABSENT | such text is escalated as uninterpreted, never guessed |
| ISO 286 fit-class resolution (`H7`, `g6`, …) | PARTIAL | 783 published cells, 37 hole and 37 shaft classes over the 11 standard size steps from 3 to 400 mm; every cell is stated identically by two independent published tables and its band equals the IT value ISO 286-2 Table 1 prints for that step. Anything else - an unloaded class, a size beyond 400 mm, a fit pair, an inch callout - keeps limits `None` and blocks the row |
| Thread callouts (`M8-6H`, `1/4-20 UNC`) | ABSENT | designation preserved for review, explicitly not converted |
| Title-block drawing number / revision fields | PARTIAL | part name, material and a labelled revision are read when present; a missing drawing number is reported as absent, and a revision the drawing never states is recorded as `revision_source = "default - the drawing states no revision"`, surfaced in the report's Limitations |
| Revision comparison | ABSENT | — |
| Interactive correction UI | ABSENT | corrections come from a JSON override file, attributed and validated |
| CAD backend integration (SolidWorks / FreeCAD / Onshape) | ABSENT | nothing calls `mac` or any kernel; no geometry is generated |
| AI/model-assisted interpretation | ABSENT (by design so far) | every value traces to the drawing, a calculation, or a named reviewer |
| Persistence database / job queue | ABSENT | flat JSON artefacts on disk |
| CI pipeline | ABSENT | commands are documented, run manually |
```


---

## Update 2026-10-17 (supersedes §2, §3 and §6 where they differ)

New packages beside `astracad_inspect`: `astracad_cad` (adapter contract, orchestrator,
SOLIDWORKS bridge over `mac`, discovery-only Creo/Fusion/AutoCAD, STEP re-reader),
`astracad_core` (task engine, request interpreter), `astracad_tol` (stacks, fits, selective
assembly), and `astracad_inspect.revision`. A git repository now exists in the cloud workspace copy
(commit history of this session); the PC folder is still not a git repository.

Measured: Windows `py -m pytest -q` → 235 passed. Live SOLIDWORKS 2026 SP04.1 runs through the
adapter: 3/3 parts SUCCEEDED with kernel and independent STEP checks (see `CURRENT_STATE.md`).

§6 changes: *CAD backend integration* is no longer ABSENT — SOLIDWORKS part creation is
END_TO_END_VERIFIED; *Revision comparison* is TESTED; *Persistence / job queue* is TESTED (SQLite).
PDF/raster/OCR, GD&T, threads, interactive UI, model-assisted interpretation remain ABSENT.
