# Provenance and the SNH'26 entry

This file states, in one place, what existed in this repository before the SpectoV National Hackathon 2026 build
window (1-6 December 2026), what was written during it, and which third-party material it carries. It exists
because the rules disqualify *undisclosed* prior work, and because a repository whose product is tamper-evidence
should not need to be trusted about its own history.

## Who wrote it

Every line of code in `src/`, `tests/`, `tools/`, `docs/` and `mecagent-alternative/` was written by the entrant
(GitHub: `@osmiumuas-wq`) on their own PC. No other person contributed code. No part of it was generated from the judges' samples,
the other entrants' work, or a template the entrant did not write.

## Timeline, as git records it

| Date | Commit | What it is |
|---|---|---|
| 2026-10-18 23:26 +0530 | `96a7fb5` | last commit before the event window: two-kernel verification, Drawing to CAD, local model planner, signed dossiers, NCR, costing |
| 2026-11-25 | (this session) | `astracad_decide`: decision records, outcome ledger, learned reliability, `tools/decide_demo.py` |
| 2026-12-01 .. 06 | (to be filled in) | the SNH'26 increment, listed below |

The pre-event history is not squashed, re-dated or rewritten. `git log` shows what it shows, and the entrant will
walk a judge through it on request.

## What SNH'26 added

*(filled in during the window; each line names the commit that introduced it)*

- Decisions surface in the local workspace: a read-only list of records and a page per record.
- Recorded outcomes on the labelled fixtures, so the calibrated number is not `withheld` in the demo.
- The local model (`AI_PROPOSED`) as an adjudication proposal on contradictions, never as a ruling.
- One-command demo, and the record of it in `docs/`.

## What it is not

- The base system is not a weekend prototype: 703 tests passed before the decision layer was added. That number is
  a liability if it is presented as six days of work and an asset if it is stated plainly.
- The 33 tests added on 2026-11-25 are also pre-window. They are not claimed as event work.

## Third-party material

| What | Where | Terms |
|---|---|---|
| Vendor sample drawings (DXF / PDF / scans) | `_sources/` | licensed sample files; **not redistributed**, not committed to a public mirror; scores only |
| Python packages | `pyproject.toml` | as licensed; `ezdxf`, `cadquery-ocp`, `cryptography`, `openpyxl`, `pypdfium2` |
| SOLIDWORKS | this PC | Student Edition, non-commercial use; drawings it produces carry the student watermark and are shown as such |
| Local model | this PC | Qwen via Strata, 127.0.0.1 only; no data leaves the machine |

## The sentence to say to a judge

"ASTraCAD is my own codebase and I started it before the event - the repository is dated, and commit 96a7fb5 is
from 18 October. During the six days I built the decision layer's remaining pieces: the workspace surface, the
outcome loop on real fixtures, and the local model as an adjudicator. The base is mine, and it is disclosed."

That version is shorter than a lie, survives `git log`, and is the one the product's own thesis demands: evidence
before assertion.