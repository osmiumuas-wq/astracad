# Dependency licences, model-use restrictions and asset provenance

Recorded 2026-10-15 per constitution §XVIII ("record external dependency licences and model-use
restrictions"). Every licence statement below was read out of the **installed distribution on this
machine**, not from a website or from memory. Reproduce with:

```
py -c "from importlib.metadata import distribution; \
       d=distribution('PyMuPDF'); [print(open(f.locate(),encoding='utf-8',errors='replace').read()[:200]) \
       for f in d.files if str(f).upper().endswith('COPYING')]"
```

Versions in this table are the ones in `requirements.lock` (measured, not declared).

## 1. Runtime dependencies

| Package | Version measured | Licence (as read from the installed licence file) | Obligation this project takes on |
|---|---|---|---|
| `ezdxf` | 1.4.4 | MIT (classifier `License :: OSI Approved :: MIT License`) | keep the notice in distributions |
| `matplotlib` | 3.11.2 | matplotlib licence agreement (BSD-style, PSF classifier) | attribution notice; the licence text ships with the package |
| `reportlab` | 5.0.1 | BSD-style, "Copyright (c) 2000-2024, ReportLab Inc., All rights reserved" | keep copyright + licence text in redistributions |
| `openpyxl` | 3.1.5 | MIT | keep the notice |
| `pypdfium2` | 5.14.0 (PDFium 156.0.8076.0) | Apache-2.0 / BSD-3-Clause (wrapper) + PDFium BSD-3 | **declared run-time PDF reader since 2026-10-17**: report read-back and vector-PDF drawing ingestion; keep the notices |
| `rapidocr_onnxruntime` (extra `ocr`) | 1.2.3 | Apache-2.0 (bundled PaddleOCR model weights: Apache-2.0) | second OCR engine for scans; brings `onnxruntime` 1.31.0 (MIT), `opencv-python` 5.0.0.93 (Apache-2.0), `shapely` (BSD-3), `pyclipper` (MIT) |
| `winrt-*` (extra `ocr`) | 3.2.1 | MIT (License-Expression in package metadata) | Python bindings to Windows.Media.Ocr, the OCR engine built into Windows; Windows-only |
| **`PyMuPDF`** (optional extra `table-rebuild` only) | **1.28.2** | **Dual licensed: GNU AGPL-3.0 **or** Artifex Commercial Licence** ("Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License") | **see §2 — this is the one that constrains distribution** |
| `pillow` | 12.3.0 | PIL/HPND-style permissive licence ("Pillow is the friendly PIL fork", © 1997-2011 Secret Labs AB, © 1995-2011 Fredrik Lundh) | keep the notice; do not imply endorsement |
| `numpy` | 2.5.4 | BSD-3-style ("Copyright (c) 2005-2025, NumPy Developers") | keep the notice |
| `fonttools` 4.67.0, `contourpy` 1.4.0, `kiwisolver` 1.5.1, `pyparsing` 3.3.3, `cycler` 0.12.1, `python-dateutil` 2.9.0.post0, `six` 1.17.0, `et-xmlfile` 2.0.0, `charset-normalizer` 3.5.2, `packaging` 26.3, `typing_extensions` 4.16.0 | as locked | MIT / Apache-2.0 / BSD-style / PSF per package; `et-xmlfile` ships `LICENCE.python` + `LICENCE.rst` | transitive, unmodified; notices travel with the wheel |

## 2. The AGPL condition on PyMuPDF — stated plainly, not buried

**Update 2026-10-17:** PyMuPDF is no longer a declared run-time dependency. `pyproject.toml`
requires `pypdfium2`; PyMuPDF is the optional `table-rebuild` extra, needed only by
`tools/iso286_extract.py` (offline rebuild of the ISO 286-2 table). `validate.py` still falls
back to PyMuPDF if pypdfium2 is absent, so a build that ships without pypdfium2 and with
PyMuPDF re-acquires the obligation below. `astracad_inspect doctor` reports which reader is live
(on the PC: `PDF REOPEN pypdfium2 (Apache-2.0/BSD-3)`).

PyMuPDF is used in exactly one place: `src/astracad_inspect/validate.py`, which **reopens the PDF
this project just wrote** and reads text/images back out of it to verify the export. It is a
verification tool, not part of the produced document.

Consequences accepted in this build:

- The ASTraCAD Inspect source is distributed as source, and linking AGPL code means **any
  distribution of this tool as a whole must be under AGPL-3.0** (or a purchased Artifex commercial
  licence must be obtained).
- Internal use at the organisation that runs this code, without distributing it, does not trigger
  the source-availability clause. That is the current situation: it runs on this PC.
- **Before any external release** (§XVIII checkpoint) one of these must happen, and it is an open
  task, not a completed one:
  1. keep PyMuPDF and publish the whole tool under AGPL-3.0, or
  2. buy the Artifex commercial licence, or
  3. swap the reopen-and-read verification to a non-AGPL reader (e.g. `pypdf`) and re-measure the
     reopen checks against the new reader. The route is deliberately isolated in `validate.py` so
     this swap is one file.
- No artefact this tool produces (PDF/XLSX/JSON/PNG) contains PyMuPDF code; the exported PDF is
  written by ReportLab.

This has been raised rather than left implicit: it is also listed in
`docs/engineering/known-limitations.md` (item 24) and in the roadmap as an M-release blocker.

## 3. Fonts embedded in deliverables

`export/pdf.py` registers **DejaVuSans** (from matplotlib's bundled fonts) as the `"Eng"` font,
because the standard PDF Helvetica silently substitutes the ⌀ glyph — measured: without it,
`⌀10 +0.05/-0.02` loses the diameter symbol in the report.

Evidence from the exported `plate_synthetic_inspection_report.pdf` (read back with PyMuPDF): every
one of its 6 pages embeds a subset named `AAAAAA+DejaVuSans` alongside non-embedded `Helvetica` /
`Helvetica-Bold` (Base-14, so no font file is shipped).

Licence, read from `matplotlib/mpl-data/fonts/ttf/LICENSE_DEJAVU`:
*"Fonts are (c) Bitstream (see below). DejaVu changes are in public domain."* The Bitstream Vera
notice grants free use including embedding in documents, with the restriction that the font is not
renamed and the trademark notice travels with it. Consequence for this project: reports embed a
**subset** of a Bitstream/DejaVu-derived font under that permissive notice; the name `"Eng"` is an
internal PDF resource name for the registered face, not a redistribution or rename of the font
file. No font file is copied into this repository.

## 4. Models and AI services

**None are used.** No LLM, OCR service, cloud API, or third-party model participates in any number
this tool produces. Consequently no model-use restriction, no rate limit, and no data-residency
obligation attaches to this build (constitution §XVII: confidential engineering documents are not
sent to external providers — here they are not sent anywhere at all).

This is a *current state*, not a permanent policy. When a model is introduced (§IX), it may only
propose: proposals enter as provenance state `ai_proposed`, and cannot become `verified` or
`human_approved` without a check or a named reviewer. Model-use terms must be recorded in this
file before that route is enabled.

## 5. Code and data this project did not take from elsewhere

- `src/astracad_inspect/**` — written in this workspace during this milestone; no code was copied
  from `mecagent-alternative` (a separate .NET codebase on this PC). The three-valued
  `CheckOutcome` design and the "measurement, not self-claim" rule were **independently adopted**
  after reading `src/MecAgent.Core/Verification.cs`.
- `tests/fixtures/*.dxf` + `.expected.json` — generated by `tools/make_fixtures.py`, i.e. synthetic
  content authored here. Contains no customer or third-party drawing data.
- `.NET 10` SDK (10.0.401) and the SolidWorks interop assemblies under
  `C:\Program Files\SOLIDWORKS Corp\` are **referenced by the sibling project only**; ASTraCAD
  links nothing from them today. SolidWorks itself is commercial third-party software under its own
  licence and is never invoked by this tool.

## 6. Published ISO 286-2 tables the deviation data was read from

`src/astracad_inspect/data/iso286-2.json` carries 783 deviation cells generated by
`tools/iso286_extract.py` from files sitting on this PC under `_sources/`. Each file is named with
its SHA-256 in that data file's `provenance` block. Their licence state, as far as it can be
verified from the saved copies:

| File | What it actually is | How it is used | Licence, as read from the file itself |
|---|---|---|---|
| `_sources/iso286_holes.html`, `_sources/iso286_shafts.html` | roymech.org ISO 286-2 hole and shaft limit tables | one of the two independent readings whose agreement a shipped cell requires | footer `&copy; 2020 Roymech.org`, **no permission grant in the saved page** |
| `_sources/iso_tolerances_tuke.pdf` | 8-page "ISO Tolerances for Shafts and Holes" table set; its own page 1 prints the URL it was saved from — `simplybearings.co.uk/shop/Info-Pages-ISO-Limits/…`, i.e. a retailer's info page, not the standard and not the filename's implied author | the second independent reading | **no licence or copyright notice anywhere in the PDF** |
| `_sources/iso286-2-sample.pdf` | a **watermarked preview copy of ISO 286-2:2010(E)** | checking only: Table 1's nominal size steps and IT values, and Table 3 as a third reading of the E-class row. `provenance.checked_against.used_for` states "no deviation read from this page is shipped here" | ISO copyrighted work; a preview copy is not a redistribution right |

What this build ships: the deviation **values** (numbers ISO 286-2 publishes, re-stated as JSON
cells with their class, size step and provenance) — not the pages, not their typography, and not
any prose. What it does not ship: none of the four files above; `_sources/` is input to the
generator, not a package directory, and nothing in `src/astracad_inspect/` reads it at runtime
(`iso286.py` loads only the generated JSON).

Open, not assumed safe (constitution §XVIII checkpoint before any external release):

- Two of the three value sources carry a copyright notice with no grant, and one carries no notice
  at all. "Publicly viewable" is not evidence of "redistributable". If the values are needed under a
  clean title, the route is a licensed copy of ISO 286-2:2010 re-extracted by `tools/iso286_extract.py`
  and the shipped cells re-verified against it.
- The watermarked ISO preview must stay off any public repository or artefact. Replacing it is the
  same task as the bullet above.
- `_sources/` currently lives inside the project directory. It has not been moved because nothing
  has been published from this workspace yet; before the first external release it should be either
  excluded from the distribution or replaced with a licensed copy, and this table re-measured.
- `requirements.lock` pins versions but not wheel hashes (§7), so the same "exact bytes" caveat
  applies to the Python dependencies as to these tables.

## 7. Licence ambiguity deliberately avoided

`requirements.lock` pins versions but **not wheel hashes**, because hashing would require a network
fetch that has not been authorised. Until hashes are pinned, "these are the exact bytes" is not a
claim this project makes — only "these are the exact versions measured on this machine."
