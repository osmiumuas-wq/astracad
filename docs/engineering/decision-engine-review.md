# Review: the engine, audited the way a judge will audit it (2026-11-25)

## Why this exists

The entry argues that a decision must be checkable against evidence. That argument only holds if the engine survives
the same treatment, so this is an audit of our own output, run against the record from the real sheet
(`_out/decide_demo/record.json`, `d-a0aa227a3cf1`). It found one false positive, one over-claim, and three places
where the confidence number says more than it has earned. Two are fixed before the base commit; the rest are tasks.

## Finding 1 - the flagship contradiction is a floating-point artifact

The record says:

```
CH-006  0.188 states 0.188000 +/- 0.000500 but the geometry measures 0.187500   blocking
```

The callout `0.188` states no tolerance, so `astracad_inspect.ingest.dxf._cross_check` uses the printed resolution
as the band: half of the last digit, `0.0005`. The intent is inclusive - `ok = abs(delta) <= slack` - and the
deviation is exactly at the edge. In binary floating point it is not at the edge:

```
abs(0.1875 - 0.188) = 0.0005000000000000004   >   0.0005   -> FAIL by 4.4e-19
```

So a value that sits exactly on the printing-resolution boundary is reported as a blocking disagreement, the
characteristic goes to HOLD, and the sheet is withheld on arithmetic noise. This is the one defect in the demo a
sharp judge would find in ten minutes, and it would discredit the whole record.

**Fix (C1).** Compare on the decimal grid the value was printed on: `Decimal(str(round(x, 12)))` on both sides, `<=`
against the band. No epsilon constant; twelve decimals is four orders of magnitude below any inspection tolerance
and far above binary noise. A value exactly on the boundary becomes a `zero_margin` warning (worth a look, not a
defect) and the check itself passes, so the coverage counters stay truthful.

**Landed 2026-11-25.** The same drawing now re-plans to `1 in disagreement` instead of 2: `CH-006` keeps a
`zero_margin` warning and a passing check, `CH-007` still blocks. Regression tests:
`test_a_value_exactly_on_the_printed_boundary_is_zero_margin_not_a_disagreement` and
`test_a_value_past_the_printed_boundary_is_still_a_finding` in `tests/test_ingest.py`. The record now carries a
row's plan warnings (`items[].notes`), so the page shows the zero margin instead of hiding the verdict's caveat.

## Finding 2 - the other contradiction is probably a bad measurement, reported as a bad part

```
CH-007  R0.09 states 0.090000 +/- 0.005000 but the geometry measures 9.697655   blocking
```

That is 107.8x the nominal, 1,921x the band. A radius of 9.7 mm against a callout of 0.09 mm is not a part that
is slightly wrong; it is a cross-check that has picked the wrong geometry - a definition point from another
entity, or a diameter-vs-radius confusion. The engine reports it as "the drawing and the part disagree", which is a
claim it cannot support. The honest report is: *this check cannot be trusted for this callout, measure it.*

**Fix (C2).** Classify the disagreement by ratio before it can block: inside the band -> pass; at the boundary ->
`at_limit`; outside but within 10x the band -> blocking disagreement; beyond 10x -> `NOT_VERIFIABLE` with the reason
`the geometric reading is not credible for this callout (107.8x nominal) - the cross-check, not the part, is the
suspect`. The disposition does not soften: it is still HOLD, because a release cannot rest on a check you do not
trust. Only the reason becomes true.

## Finding 3 - nine unjudged callouts do not touch the confidence

The record judges 3 of 12 callouts and reports nine `NOT_JUDGEABLE`. The confidence floor is `0.70`, computed from
the weakest of five decisive nodes, and nothing in `_confidence` prices the 75% of the sheet that was never judged.
If the plan gate had been open, `approvable` would have released it with a footnote. That is the exact failure the
statement asks us to solve - missing information presented as a pass with a caveat.

**Fix (C3).** Add `coverage` to the `Confidence` record: `judged / total`, and a policy rule - a RELEASE needs
`coverage >= RELEASE_COVERAGE` (0.80) or a named human waiver on the remainder; otherwise HOLD, with the sentence
"n of m callouts could not be judged; release needs a person to accept that gap". The number stops being a floor
over what we happened to look at and becomes a floor over what we actually know.

## Finding 4 - the calibration number is noisier than it looks

`(confirmed + 1) / (n + 2)` at n = 12 is a point estimate with a 95% interval roughly +/- 0.25 wide. Presented as a
bare number in a demo, it invites a judge to read two decimals where one significant figure is honest. The Brier
score is also additive-only: it cannot say whether the confidence beats a constant.

**Fix (C4).** Report the Wilson interval beside every calibrated value, a reliability table with per-bin `n` and
interval, and a Brier *skill* score against the always-0.5 baseline. Refuse to draw the curve below n = 20; below
that, print the table and the interval and call it preliminary.

## Finding 5 - the weights are asserted, and the ruling may depend on them

`geometry_cross_check 0.90`, `source_parse 0.70`, `model_proposal 0.40` - these are priors with a rationale in the
docstrings and no sensitivity analysis. A judge is entitled to ask what happens at 0.80.

**Fix (C5).** A sweep: perturb every decisive weight by +/-0.10, re-decide, and record `stable: true` with the
smallest perturbation that would flip the ruling, or `unstable` with the pair that flips it. Put the sentence in the
record: "the ruling survives +/-0.10 on every weight" or "it flips if geometry is trusted below 0.82". That converts
an asserted number into a testable one in one page of code.

## Finding 6 - a whole-file rewrite still verifies

The chain catches an edit inside a record and cannot catch a reader who rebuilds every record from scratch, because
a self-contained file contains no external anchor. Left as is, this is the one honest limit in the design; there is
a cheap fix that does not need keys:

**Fix (C6).** After each decide run, write the head hash into a git commit message (`decide: head <hash>`). A
rewritten file then disagrees with history, which is outside the attacker control of the file. One line of code,
one line of process, and the limit moves from "cannot detect" to "detectable by a third party".

## What this does to the story

Before: twelve callouts, two contradictions, nine unjudged. After C1: one boundary case (zero margin), one blocking
contradiction, ten unjudged, fourteen absences, same HOLD, new record id `d-0f9216f47ad6`. After C2 the blocking
one becomes a named untrustworthy reading, which is still a HOLD for a different and truer reason.

The slide that lands: **the audit found our own false positive before the event, and the fix is in the base
commit.** Every other entry will claim its checks are right. This one can show the commit where they stopped
being wrong.

## Order and cost

| Task | Where | Cost | When |
|---|---|---|---|
| C1 decimal boundary | `astracad_inspect.ingest.dxf` | 2 h | with C2 and C3, before the base commit |
| C2 implausible ratio | `astracad_inspect.ingest.dxf` | 1 h | with C1 and C3, before the base commit |
| C3 coverage policy | `astracad_decide` | 3 h | with C1 and C2, before the base commit |
| C4 calibration intervals | `astracad_decide` | 3 h | in the window |
| C5 weight sensitivity | `astracad_decide` | 2 h | in the window |
| C6 head anchored in git | tooling | 30 min | in the window |

**C1, C2 and C3 land together or not at all.** C1 alone removes one blocking finding, C2 alone removes the other,
and a sheet with ten unjudged callouts and no blocking finding would be released by the current rule. The coverage
policy is what keeps the gate shut once the two false findings stop holding it. Sequencing them apart would turn a
correct fix into a looser gate.

## What not to do

Do not re-run the demo and quietly swap the numbers. The record ids change when the plan changes, so the deck must
cite the new id, and `NEXT_ACTION.md` must say which finding caused the change. A record that hides its own
correction is the thing this entry exists to argue against.