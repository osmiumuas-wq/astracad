# Evaluation results

Only recorded runs appear here.

| Date | Benchmark | Dataset | Result | Where |
|---|---|---|---|---|
| 2026-10-15 | Inspection extraction | 2 synthetic DXF (12 labelled chars) | recall 1.0, precision 1.0, field acc 1.0 | `_out/regression_report.json` (PC) |
| 2026-10-14 | SolidWorks part specs | 8 specs in `mecagent-alternative/specs/batch` | 8/8 jobs, analytic volume deltas ≤ 1e-11 mm³ on prism family | `mecagent-alternative/work/*.report.json` |
| 2026-10-17 | Tolerance engine | closed-form references in `tests/test_tol.py` | 19/19 pass; MC sigma within 1% of RSS closed form | `tests/test_tol.py` |
| 2026-10-17 | ASTraCAD -> SOLIDWORKS (real app) | 3 interpreted requests | 3/3 SUCCEEDED; block polyhedral V re-read = kernel = analytic 48000 mm³ | `_win/sw2/*.log` (PC) |

Synthetic results do not predict accuracy on production drawings (M2 corpus not yet acquired).
No reference-product run exists, so no comparative result exists.
