# The plan (2026-11-25) - the one to execute

**One sentence.** A release decision that names every callout it cannot judge, refuses to release on a gap,
learns which source lied last time - and audits its own checks in public.

**The wedge.** Not *does a source support the claim* - *does the part support the claim*. Geometry is the only
source on an inspection sheet that does not depend on what a human typed.

**The line.** AI proposes, evidence decides, a person approves - and the engine learns which source lied last time.

This supersedes nothing and duplicates nothing: the task-level detail stays in `snh26-plan.md`, the loss modes in
`win-audit.md`, the audit in `decision-engine-review.md`. This file is the order of operations.

## 1. What is already true, measured

| claim | evidence |
|---|---|
| the engine rules, records and refuses | `_out/decide_demo/record.json`, `d-0f9216f47ad6`, HOLD |
| the suite is green | `py -m pytest -q` -> **739 passed**, 197.23 s (2026-11-25) |
| the judged path is offline and stdlib-only | `py tools/preflight.py` -> **11/11**, 195 tracked files, no key-shaped strings |
| the sheet is real, not a fixture | `_sources/corpus_sw/acad_7550_021_s1.dxf`, 12 callouts |
| one false positive was found and fixed | `docs/engineering/decision-engine-review.md`, finding 1, landed |
| the ruling after the fix | 1 contradiction, 10 unjudged, 14 absences, floor 0.70 |
| the base predates the window | HEAD `96a7fb5` (2026-10-18); 25 paths uncommitted |

The base commit has not been made. Nothing in December counts as "built during the event" until it is.

## 2. Where the hundred points are

| criterion | weight | what earns it | where it lives | status now | target |
|---|---|---|---|---|---|
| Technical | 25 | evidence nodes, hash chain, two confidence numbers, a measured self-audit | `astracad_decide/` | built | 21 |
| Innovation | 20 | the part-not-the-document wedge; an agent whose job is to attack the record | A2, A3 | missing | 17 |
| Problem solving | 20 | a real sheet, ten callouts nobody can judge, the coverage rule | C1-C3 | partial | 17 |
| UX | 15 | one page, read in fifteen seconds, no typing, "what would change my mind" | Task 6 | partial | 12 |
| Scalability | 10 | stdlib, O(1) append, offline, a batch number you can say out loud | Task 7 | partial | 8 |
| Presentation | 10 | five minutes rehearsed twice, video fallback | Task 9 | missing | 9 |
| **total** | 100 | | | | **84** |

Those are targets, not promises. A target is a place to spend the next hour; a promise is a lie with a
deadline.

## 3. Why this order

The order is points per hour, and it starts with the two things that cannot be faked on the day: the ruling
being true, and a stranger reading the page. The agent layer earns its place because a *red* agent is the only
part of the entry a judge has never seen, and because it runs in replay - no network, no key, no luck.

The audit beat is the asymmetry. Any team can claim its checks are right; this one has a commit where they
stopped being wrong. It costs one slide and it cannot be copied inside the window.

## 4. Before the window (now to 30 November)

1. **Register.** 250 seats, first come. Nothing else in this document matters without it. (30 min)
2. **Fill `<NAME>`** in `PROVENANCE.md`; state the third-party table honestly. (15 min)
3. **Commit the base**: 25 paths plus this file, dated 25-28 November, message naming what was pre-existing.
   (20 min)
4. **Re-read the rules** for the disclosure clause and for external model APIs; write the answer into
   `PROVENANCE.md` so the decision is documented before it is questioned. (20 min)
5. **Book the rehearsal partner**: one person who does not know the project, 20 minutes on 5 December. (5 min)

## 5. The window, 1-6 December

Two blocks a day, three hours each, one exit criterion per day. If a block overruns, the day is cut, not
extended - the cut line in section 6 decides what goes.

### D1 (1 Dec) - tell the truth about both numbers: C2, C3

- C2: a reading beyond 10x the band is `NOT_VERIFIABLE`, not "the part is wrong"; the characteristic goes
  `unresolved`, the sentence names the check as suspect. Test both sides of the ratio.
- C3: `Confidence.coverage` (a row that received a decision - ACCEPT or HOLD - over all rows), `RELEASE_COVERAGE =
  0.80`, and a release that refuses with "n of m callout(s) could not be judged". Test a plan that would otherwise
  release.
- **Exit:** the sheet re-plans with **0 blocking**, coverage `0.1667`, HOLD, and a reason that is true.

### D2 (2 Dec) - the agents that gather: A1, A2

- A1: `agents/trace.py` - one JSONL line per step (run, step, agent, mode, prompt hash, status, ms, evidence ids),
  append-only, deterministic. A step that did not run is a line with a reason.
- A2: `agents/evidence.py` - geometry, standards, document, history. Each returns `AI_PROPOSED` at weight 0.40.
- **Exit:** four agent lines on the real sheet, zero disposition changes, and a test proving no proposal can lift
  a blocking finding.

### D3 (3 Dec) - the agent that attacks: A3

- A3: conductor (fixed order, gates, budgets) and the red agent. Red reads **only the record JSON** and emits
  findings; each becomes an evidence node at 0.35, and a red finding cannot be waived silently.
- **Exit:** at least one real gap found (or a documented clean run), and the trace shows the attack line by line.

### D4 (4 Dec) - frontier proposals, offline replay: A4 + one workflow

- A4: `providers.py` with `local | anthropic | recorded`; transcript = prompt hash, model, tokens, ms; a
  `ReplayMiss` on any prompt not in the file; the key is never written.
- One workflow: `release_triage` as a JSON DAG with gates and budgets; a skipped step is a line, not a silence.
- **Exit:** the replay run works with sockets disabled and produces the same bytes twice.

### D5 (5 Dec) - the fifteen seconds: Task 6, C5, C4-lite

- Page: ruling, the one contradiction, coverage, floor, and "what would change my mind" - no typing, no scroll on
  a 1080p laptop.
- C5: +/-0.10 weight sweep, printed as "the ruling survives" or "it flips below 0.82".
- C4-lite: Wilson interval beside the calibrated value; no curve below n = 20.
- **Exit:** a stranger reads for fifteen seconds and states the ruling and one reason, unaided.

### D6 (6 Dec) - freeze and ship

- 09:00 full suite, preflight, demo, video take 2, deck.
- 11:00 submission assembled. 12:00 sent. Six hours of buffer.
- **Exit:** three copies (submission, video, zip), all dated.

## 6. The cut line

Cut in this order when behind: A4 Anthropic recording (keep local + recorded) -> C4-lite -> C5 ->
the `release_triage` DAG (keep the conductor) -> A2 history agent.

Never cut: the page, the disclosure, the rehearsal, the video, the coverage rule.

## 7. The five minutes

| time | on screen | the sentence |
|---|---|---|
| 0:00 | the sheet, `R0.09` circled, `9.697655` beside it | "This sheet is one approval away from release." |
| 0:25 | 12 callouts, 10 unjudged | "Ten of these can never pass or fail. A checker shows you a green sheet." |
| 0:55 | the record page: HOLD, one contradiction | "It says what it cannot support, and who must decide." |
| 1:25 | `0.188` row, zero margin | "Yesterday this was a blocking defect. It was 4e-19 of float. We found it, fixed it, and the commit is dated before the event." |
| 1:55 | the red agent attacking the record | "The last agent's job is to break this. It reads only the record." |
| 2:35 | one trace line | "Who, which mode, which prompt hash, what it claimed." |
| 3:05 | floor 0.70 -> 0.7556 | "A weight, not a vibe." |
| 3:35 | approve / waive, two-person rule | "It proposes at 0.40. A rule above every weight stops it. A person signs." |
| 4:05 | 200 sheets, one laptop, no network | "Twelve sheets or two hundred; no network, no key." |
| 4:30 | the record | "Evidence decides, a person approves, and the record says what it could not judge." |

## 8. The risks, with the response already written

| trigger | response |
|---|---|
| no seat left | register today; keep building - the repo and the write-up stand on their own |
| wifi dies | replay mode; the video is the demo |
| no model key | local provider; say "local" in the trace and in the deck |
| "did the AI decide?" | show weight 0.40 and the gate rule that outranks every weight |
| "your data is synthetic" | one real sheet, one real defect, and the disclosure paragraph |
| overrun at 3:30 | cut the agent beat, keep the audit beat |
| laptop dies | second machine, zip on a stick, video on the phone |

## 9. Definition of done, with the commands

- [ ] `py -m pytest -q` - 739+ passed
- [ ] `py tools/preflight.py` - 11/11
- [ ] `py tools/run_demo.py` - exit 0 with `ASTRACAD_MODEL_ALLOW_REMOTE` unset
- [ ] `py -m astracad_decide verify <store>` - chain intact
- [ ] two rehearsals, 5:00 +/- 15 s, one with a stranger
- [ ] every claim in the deck has a command or a file behind it
- [ ] `PROVENANCE.md` filled, dated, disclosed
- [ ] submitted by 12:00 on 6 December, three copies

## 10. The honest ceiling

A top-three finish needs four things: a seat, a judge who opens the record page, five minutes that land, and no
disqualification. The last two are mostly in hand. The second depends on a page nobody has to be taught to read.
The first is outside my control and is the only part of this plan that can fail for a reason unrelated to the
work.

## 11. Do not

- Do not add a feature after D5. Freeze means freeze.
- Do not put SOLIDWORKS or scan OCR on the judged path.
- Do not type live. Do not open an editor in the demo.
- Do not claim the model is the product; the record is the product.
- Do not present rehearsal outcomes as results. They are labelled synthetic in code, page and deck.