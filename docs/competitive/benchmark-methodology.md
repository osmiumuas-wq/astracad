# Benchmark methodology

A claim of parity or superiority is only made against a **defined dataset, a defined metric and a
recorded run**. Until a row below has all three, its status stays at or below `TESTED`.

## Status ladder (constitution Part 2)

`NOT_INVESTIGATED → INVESTIGATED → PLANNED → IMPLEMENTED → TESTED → VERIFIED →
PARITY_DEMONSTRATED → SUPERIORITY_DEMONSTRATED`

- `TESTED`: automated tests exist and pass in a recorded run.
- `VERIFIED`: an independent check (reopen-and-read, analytic expectation, kernel read-back) agrees.
- `PARITY_DEMONSTRATED`: the same task set was run on ASTraCAD **and** on the reference product, and
  ASTraCAD met the reference's result on every acceptance criterion. Requires lawful access to the
  reference product. **Neither reference product has been run by us** (MecAgent: installer behind
  login; Pre6: login, not attempted), so no row can reach this level today.
- `SUPERIORITY_DEMONSTRATED`: as above plus a measured advantage on a stated metric.

## Inspection (Lens-class) benchmark — defined, not yet run against a reference

Dataset: labelled drawings in `tests/fixtures/*.expected.json` (currently 2 synthetic) plus the M2
real-drawing corpus (not yet acquired). Metrics, per drawing, with denominators:
recall, precision, strict field accuracy (nominal, limits, unit, kind), balloon-mapping correctness,
duplicate count, invented-limit count (must be 0), time to deliverable.
Harness: `tools/regression.py`.

## CAD-automation (MecAgent-class) benchmark

Task specs in `mecagent-alternative/specs/*.json`; each carries an analytic expectation
(volume, area, centre of mass, dimension read-back). Metric: kernel-measured deviation from the
analytic value, artefact reopen success, operation success rate. Harness: `mac run` / `mac batch`
and, from this session, `astracad_cad` orchestrator.

## Tolerance-engine benchmark

Reference calculations written independently in the tests (hand-derived closed forms and a fixed
seed Monte Carlo cross-checked against the RSS closed form). Metric: absolute error vs reference.
