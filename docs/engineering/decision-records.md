# Decision records (2026-11-25)

A release gate that prints `HOLD` is a verdict with no paper behind it. A week later nobody can say what it
relied on, what it did not have, or whether it has been right before. `astracad_decide` writes the paper: one
append-only record per ruling, carrying the evidence for it, the evidence against it, the information that was
missing, and a confidence that says which of those numbers it is.

## Why

The existing gate (`plan["approvable"]`, `plan["gate"]`) answers "may this be released" and nothing else. Three
questions arrive the moment a plan is challenged, and the gate answers none of them:

| Question | Before | Now |
|---|---|---|
| What did the ruling rely on? | a gate string | every decisive evidence node, by id, with a weight and an author |
| What was **not** known? | nothing recorded | each absent limit, uninterpreted callout, unstated revision, listed as an item with what it blocks |
| Have we been right before? | no memory | a per-evidence-kind frequency, charged on every outcome, which changes the next floor |

The rule the whole module is built around: **a claim that cannot be traced to a file is not made**. There is no
field in the record that can hold an unsupported opinion, and the engine refuses a plan that carries no
`source_sha256` at all.

## The truth table

One row per characteristic, first match wins:

| Condition | Ruling | Because |
|---|---|---|
| a reviewer rejected it | `REJECT` | a person said no; the engine does not argue |
| a check failed | `HOLD` | a measured or computed failure is not a permission slip |
| its state is `unresolved` | `NOT_JUDGEABLE` | the sheet was never read into a value |
| it has no limits | `NOT_JUDGEABLE` | a value with no limit can be recorded but never passed |
| a person approved it | `ACCEPT` | the approval is evidence, tagged as human, and stays `waived` if it was an exception |
| otherwise | `ACCEPT` | the check passed and the limit exists |

A characteristic with no limits is never `ACCEPT`. That single line is what separates this from every plan
generator that prints a tick when it means *not measured*.

The plan ruling is decided separately:

| Condition | Ruling |
|---|---|
| no characteristics at all | `NOT_EVALUABLE` |
| every characteristic `REJECT` or `NOT_JUDGEABLE` | `REJECT` |
| a blocking contradiction | `HOLD` |
| `approvable` | `RELEASE`, with the count of callouts that could not be judged appended to the rationale |
| otherwise | `HOLD`, citing the gate |

`NOT_EVALUABLE` exists so that "we could not look" is never filed as "we looked and it failed".

## Two numbers, never one

`astracad_inspect.model` already refuses to collapse provenance into a single score. The decision layer keeps
that discipline and adds only what a decision needs:

- **floor** - the weight of the weakest *decisive* node. Five weights, but the ruling only stood on the 0.7 one,
  so the record says `0.7`. It is a ceiling on how much any part of the ruling can be trusted, not an average.
- **calibrated** - the observed frequency of rulings of this disposition holding, as a Beta posterior
  `(confirmed + 1) / (n + 2)`, and only once `n >= 5`. Below that the record says `withheld` and says why.

Every weight is a stated prior (`EVIDENCE_WEIGHTS`: geometry cross-check 0.90, limits derived 0.85, human review
0.95, layout check 0.80, title block 0.75, source parse 0.70, OCR 0.60, human waiver 0.50, model proposal 0.40).
None of them were fitted to anything. They are the honest part of the design that a judge should push on, and
the doc says so rather than dressing them up as measurements.

## The loop, and who gets charged

    decide  ->  outcome (CONFIRMED / OVERTURNED)  ->  reliability  ->  the next decide uses the learned weights

- `CONFIRMED` credits every decisive evidence kind.
- `OVERTURNED` charges exactly **one** kind. The person filing it names the node that was wrong; if they cannot,
  the engine charges the weakest decisive node and records that it did so. Naming a node that is not part of the
  decision is refused - you cannot blame evidence that was not used.
- A kind is only believed once it has `n >= 3` outcomes; before that it keeps its prior and the table says
  `prior only`.

No outcome, no movement. There is no path in which the engine quietly improves its own numbers.

## The chain

Records are JSONL, one per line, each carrying `prev_hash` and its own `hash` over the canonical payload
including `prev_hash`. `verify` re-derives every hash, checks the chain, re-checks that each `decision_id`
derives from that record, and - when given a plan - that at least one record was decided on that plan. It
names the defect rather than the fact of failure:

| Defect | Meaning |
|---|---|
| `hash does not recompute - the record was edited` | a field was changed after the ruling |
| `does not chain to the record before it` | a record was inserted, removed or reordered |
| `id does not derive from its own content` | the id was transplanted from another record |
| `no record in this file was decided on the supplied plan` | the store belongs to something else |

This is tamper *evidence*, not tamper *prevention*: anyone who rewrites the whole file from scratch produces a
chain that verifies. Closing that gap is the existing Ed25519 dossier signing, over the file, not over each line.

## What it does on a real sheet

`_out/layout_after/acad_7550_021_s1_dxf/acad_7550_021_s1.plan.json`, 12 callouts:

    py -m astracad_decide decide <plan.json> --actor "K M"
    d-0f9216f47ad6 HOLD 1 contradiction(s) 14 unknown(s) floor=0.7

- callouts: 1 `ACCEPT`, 1 `HOLD`, 10 `NOT_JUDGEABLE` (ten callouts carry no limit).
- contradiction: `CH-007` states `R0.09` where the geometry measures `9.697655`, `VALUE_VS_GEOMETRY`, severity
  `blocking`. `CH-006` states `0.188000 +/- 0.000500` where the geometry measures `0.187500`: exactly on the printed
  boundary, reported as `zero_margin` (warning) rather than as a disagreement.
- 24 evidence nodes, floor `0.70`, bounded by `ev:release_gate_blocking_findings:plan`.
- calibrated `withheld`, 0 outcomes recorded.

The hit is a real defect of the kind a checker built on the same parser would have passed: the *stated* value and
the *measured* value disagree, and it was on the sheet in front of a human. The boundary case is the other half of
the argument - a person releasing at zero margin should be told so, and should not be told a defect exists
(`decision-engine-review.md`, finding 1).

## SNH-AI-05: what TrustChain AI asks for, and where it lives

| Required element | Where | How |
|---|---|---|
| evidence identification | `engine.evaluate` -> `Decision.evidence` | every claim becomes a node with an id, a kind, an origin and a stance |
| provenance | `source_sha256` on each node, `prev_hash`/`hash` per record | SHA-256 of the plan and of the record that preceded it |
| machine-readable trace | `Decision.payload()`, `record.json` | canonical JSON, one file, replays to the same id |
| conflict detection | `_contradictions` | `VALUE_VS_GEOMETRY`, `SOURCE_DISAGREEMENT`, `DUPLICATE_CALLOUT`, each with both sides |
| confidence estimation | `_confidence` | floor from decisive weights; calibrated Beta, withheld below 5 outcomes |
| missing-information detection | `_unknowns` | `no_limits`, `uninterpreted_characteristic`, `uninterpreted_annotation`, `unplaced_balloon`, `revision_not_stated` |
| predicted vs actual | `outcome.record` | each outcome carries the predicted disposition and the actual one |
| error attribution + updating | `outcome.attribute`, `outcome.reliability` | one kind charged per overturn; per-kind Beta posterior feeds the next floor |

## Limits, stated plainly

- The weights are priors. Nothing fit them. What changes them is outcomes, and only after three per kind.
- Calibration is a frequency over rulings, not a probability that this part is good. It answers "how often has a
  `HOLD` from this engine held", nothing more.
- The loop needs outcomes. Without recorded verdicts the module still traces, contradicts and enumerates, and
  the calibrated number stays `withheld` forever rather than being invented.
- SOLIDWORKS and scan OCR are deliberately outside the judged path: both need Windows and a licence (or the
  Windows-only OCR API), and a demo that dies on a judge laptop is worse than one that never needed them.
- Decisions are keyed to a plan file, not to a drawing. Re-parse the same DXF and the id changes unless the plan
  bytes are identical - the id derives from the plan, which is the artefact actually ruled on.

## Evidence (2026-11-25)

- `py -m pytest tests/test_decide.py -q` - 33 passed (32 engine/CLI/report, 1 page generator).
- `py -m pytest -q` - 736 passed in 199 s, 0 failed (suite total before this module: 703).
- `py tools/decide_demo.py <plan>` - writes `_out/decide_demo/index.html` from the run above; the page labels its
  five outcomes as a rehearsal in as many words, because they are synthetic and a rehearsal may never be filed
  as a result.
- `py -m astracad_decide verify <store> --plan <plan>` - `OK 1 record(s); chain intact, decided on this plan`.
- Running `decide` twice appends nothing the second time: `unchanged: d-a0aa227a3cf1 is already the latest
  record in decisions.jsonl (nothing appended)`.