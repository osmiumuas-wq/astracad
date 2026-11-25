# ASTraCAD Ω

**A self-auditing decision engine for engineering inspection plans.**

> AI proposes, evidence decides, a person approves — and the engine learns which source lied last time.

Most tools answer *does this source support the claim?* This one answers *does the part support the claim?* —
and when two independent sources disagree about the part, it withholds the ruling instead of averaging them.

Live page: **https://osmiumuas-wq.github.io/astracad/** · Built for **SNH'26**, AI track (Registration `SNH-9Z7SG3BQ`).

---

## See it in one command

Python 3.11+ (tested on 3.14). The decision demo is **standard library only** — no `pip install`, no model API key, no network,
no CAD application.

```powershell
git clone https://github.com/osmiumuas-wq/astracad
cd astracad
$env:PYTHONPATH = "src"
py tools/decide_demo.py examples/acad.plan.json
start _out/decide_demo/index.html
```

```bash
# same thing on Linux/macOS
PYTHONPATH=src python tools/decide_demo.py examples/acad.plan.json
```

The command writes a decision record: the ruling, the evidence behind every callout, what is missing, the hash chain,
and the effect of five recorded outcomes on each source's weight. Verified to run from a bare copy of `src/`, `tools/`
and `examples/` — nothing else on disk is required.

## What it decides

A plan is not released because it looks complete. Each characteristic resolves to **ACCEPT**, **HOLD**, **REJECT** or
**NOT_JUDGEABLE**, with its reason attached:

| Ruling | Means |
| --- | --- |
| `ACCEPT` | Every premise is supported by evidence at or above the required state, and nothing is missing. |
| `HOLD` | A person must arbitrate — usually because two independent sources disagree, or a premise is weaker than the decision needs. |
| `REJECT` | The evidence contradicts the claim. |
| `NOT_JUDGEABLE` | There is nothing to judge: no definite limits, so a measurement here could be recorded but never judged. |

Confidence is computed two ways on purpose: a **floor** from the weakest fact (`method="weakest_link"`), and a
**calibrated Beta** posterior per source, `(confirmed + 1) / (n + 2)`, which stays at its stated prior until five
outcomes exist and three per kind. The engine will not pretend to be calibrated on n=1.

## The invariants

1. **A model may propose; it may never approve.** Model output enters as `AI_PROPOSED`. `EXTRACTED`, `PARSED`,
   `CALCULATED`, `VERIFIED` and `HUMAN_APPROVED` are written by evidence and people.
2. **Missing is reported missing.** An absent limit, revision or calibration is a first-class finding, never blank space.
3. **Every fact has provenance.** Append-only JSONL, SHA-256 per record; editing a line breaks the chain and the
   verifier exits non-zero (exit 4).
4. **Release is a signed, two-person act.** Dossiers are Ed25519-signed under a two-person rule.
5. **Refusal is a designed outcome.** An unsupported release exits 2, a broken chain exits 4; both are enumerated
   and tested.

## Repository map

| Path | What lives there |
| --- | --- |
| `src/astracad_decide/` | The decision layer: model, engine, outcomes, report, CLI (stdlib only) |
| `src/astracad_inspect/` | DXF / vector-PDF / scan ingestion, limits, balloons, exports |
| `src/astracad_core/` | Persistent task engine, request interpreter, projects |
| `src/astracad_cad/` | Versioned CAD adapter contract; SOLIDWORKS over `mac` (Windows, off the judged path) |
| `src/astracad_web/`, `src/astracad_mfg/`, `src/astracad_tol/`, `src/astracad_geom/`, `src/astracad_lib/` | Workspace UI, manufacturing, tolerancing, geometry, shared library |
| `tools/` | Demo, preflight, corpus tooling |
| `tests/` | The suite (see *Status*) |
| `site/` | The published page (`index.html` landing, `record.html` generated artefact) |
| `docs/` | Architecture, engineering reviews, SNH'26 plan |

## Status

Measured on 2026-11-25, on this machine, by the commands named:

| Fact | How it was measured |
| --- | --- |
| **739 tests passing**, 197 s | `py -m pytest -q` with `PYTHONPATH=src` |
| Decision demo runs from a bare source copy | `py tools/decide_demo.py examples/acad.plan.json` in a scratch directory |
| 11/11 preflight checks pass | `py tools/preflight.py` |
| Ingestion verified on 2 synthetic drawings + a vector-PDF rendering + the SOLIDWORKS corpus | ingestion tests and corpus runs |

## Honest limits

- **Prototype, not production.** Verified end-to-end on synthetic drawings and a SOLIDWORKS corpus; not on a live
  drawing office's backlog.
- **The five outcomes in the demo are a rehearsal** and the page says so in as many words. They exist to show the
  learning loop move; they are not measurements of a real part, and a rehearsal may never be filed as a result.
- **The Windows COM path to SOLIDWORKS is off the judged path.** The decision engine needs no CAD application.
- **No claim of a win, a benchmark crown or a client.** The claim is a traceable ruling that shows its work.

## Documentation

- `docs/engineering/decision-records.md` — what a decision record is and why the fields are shaped this way
- `docs/engineering/decision-engine-review.md` — the adversarial review that produced the current design
- `docs/snh26/ULTIMATE-PLAN.md` — the build plan for the hackathon window
- `docs/snh26/submission-blurb.md` — the short pitch

## Licence and provenance

See `PROVENANCE.md` for authorship and the commit trail. Prior work is disclosed rather than hidden: this
repository predates SNH'26 and the hackathon builds on it, which the official rules allow when it is disclosed.
