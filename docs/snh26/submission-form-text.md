# SNH'26 — submission form answers (2026-11-25)

Copy-paste source for the SNH'26 submission platform (rules §09). Keep every link openable without a login; §10
makes broken links the participant's problem, not SpectoV's.

| Field | Value |
| --- | --- |
| Registration ID | `SNH-9Z7SG3BQ` |
| Participant | Hemanth Prasanth KM |
| Track | Artificial Intelligence |
| Project title | ASTraCAD Ω — Self-Auditing Decision Engine |
| GitHub / source | https://github.com/osmiumuas-wq/astracad |
| Live demo | https://osmiumuas-wq.github.io/astracad/ |
| Decision record (direct) | https://osmiumuas-wq.github.io/astracad/record.html |
| Demo video | _to record — see `demo-video-script.md`_ |
| Documentation | `README.md` + `docs/engineering/decision-records.md` (link them, do not paste walls of text) |

> Note: the GitHub field on the **registration** form was the participant profile (`github.com/osmiumuas-wq`).
> The **submission** form wants the *project repository* — `github.com/osmiumuas-wq/astracad`. Different field,
> different purpose; do not paste the profile there.

## Problem statement

Engineering inspection plans are signed off on the strength of documents that disagree with each other. A drawing says
one thing, a parsed extractor says another, a revision note says a third, and the person releasing the plan has no
place to see *which* premise the release actually rests on. Existing tools answer *does a source support this claim?* —
none answer *does the part support this claim, and which source lied last time?* The result is silent averaging:
disagreements get smoothed into a single plausible number, and the release decision inherits a confidence nobody
computed.

## Solution overview

ASTraCAD Ω is a decision engine that treats a release as an **auditable ruling**, not a score. Every fact carries an
evidence state (`USER_SPECIFIED`, `EXTRACTED`, `PARSED`, `CALCULATED`, `AI_PROPOSED`, `VERIFIED`, `HUMAN_APPROVED`,
`UNRESOLVED`, `REJECTED`, `SUPERSEDED`) and a SHA-256 provenance record in an append-only log. The engine:

1. resolves each characteristic to ACCEPT / HOLD / REJECT / NOT_JUDGEABLE with the reason written beside it;
2. withholds the whole release when independent sources disagree, handing the arbitration to a person instead of
   averaging;
3. computes confidence as the **weakest link** across the premises, not the mean of the strong ones;
4. learns a calibrated Beta weight per source from recorded outcomes — `(confirmed + 1) / (n + 2)` — held at its
   stated prior until five outcomes and three per kind exist;
5. reports what is missing as a finding (no limits, no revision, no calibration) rather than leaving a blank.

A model may propose (`AI_PROPOSED`); it may never write `VERIFIED` or `HUMAN_APPROVED`. Release is a signed,
two-person act. A tampered log makes the verifier exit 4; an unsupported release exits 2.

## Key features

- Ruling-first output: a decision a person can defend, with the evidence attached to every line.
- Disagreement as a first-class result: HOLD plus the arbitration, never a merged number.
- Weakest-link confidence floor, plus per-source calibrated weights that move as outcomes accumulate.
- Append-only, hash-chained provenance; tamper detection that fails loudly.
- Missing-evidence reporting as a designed outcome.
- Runs on the Python standard library for the decision path — no model API key, no network, no CAD application.

## Technology stack

Python 3.11+ (tested on 3.14). Decision layer is standard-library only; the wider prototype uses `ezdxf`, `reportlab`,
`openpyxl`, `pypdfium2`, `Pillow`, and optionally `cryptography` for Ed25519 dossiers. Modular monolith under `src/`,
single repository, no second build tree. Front-end artefacts are self-contained HTML with inline CSS.

## Technical architecture

`drawing (DXF / vector PDF / scan) → astracad_inspect (characteristics, limits, GD&T) → astracad_decide (evidence
states, disagreements, confidence floor, calibrated weights) → decision record (JSONL chain + rendered page) →
human approval (two-person, signed) → release or HOLD`.

Judged path: `src/astracad_decide/`. The Windows COM path to SOLIDWORKS lives in `src/astracad_cad/` and is not
required to reproduce any result in the submission.

## AI / model / dataset details

- No hosted model is called at run time. "AI" here is the decision layer: evidence states, provenance, calibrated
  source weights, and a strict rule that model output enters only as `AI_PROPOSED`.
- Weights are Beta posteriors over recorded outcomes with explicit gates (`MIN_OUTCOMES = 5`,
  `MIN_OUTCOMES_PER_KIND = 3`). Before the gate, the stated prior is used and labelled as such.
- Datasets: two synthetic DXF drawings, a vector-PDF rendering of one, and a SOLIDWORKS corpus. The five outcomes
  shown in the published record are a **rehearsal**, labelled on the page — not measurements of a real part.

## What the judge should click, in order

1. `https://osmiumuas-wq.github.io/astracad/` — the landing page, 60 seconds.
2. `…/record.html` — the live decision record: the HOLD ruling, per-callout reasons, the chain, the weight shift.
3. `https://github.com/osmiumuas-wq/astracad` — README, then `src/astracad_decide/engine.py`.
4. Optional: clone and run the one command in the README. No dependencies for the demo.

## Pre-submit checklist

- [ ] Repo public, README renders, default branch `main`.
- [ ] Pages URL returns 200 in a private/incognito window.
- [ ] Video link opens without a login (unlisted is fine).
- [ ] Registration ID correct: `SNH-9Z7SG3BQ`.
- [ ] No `localhost`, no `file:///`, no permission-gated Drive links anywhere in the form.
- [ ] Everything still opens on 14 December, not just on 6 December.
