# The audit: how this entry loses, ranked (2026-11-25)

No plan makes a win certain. What a plan can do is make this the entry that is hardest to **dismiss** - the one
where a judge cannot write down a reason to score it low. Every row below is a sentence a judge could write, and
the column that matters is the last one: the artefact that makes the sentence impossible.

| # | The sentence a judge writes | How it shows up | Kill it with | Proof it is dead |
|---|---|---|---|---|
| 1 | "the numbers are made up" | a confident figure with no history behind it | thresholds: 3 per kind, 5 per disposition; `withheld` with the reason | the page shows `withheld` and why, and the doc explains it before anyone asks |
| 2 | "the demo breaks on my machine" | nothing installed, no network, a cold account | stdlib-only layer, injected gateway, one script | `tools/preflight.py` output pasted into `DEMO.md` |
| 3 | "it cannot even start" | a path with spaces, a `cp1252` console, `⌀` in the report | `utf8_console()`, pathlib everywhere | preflight checks 1, 6, 9 |
| 4 | "where is the AI?" | a rules engine with a model bolted on the side | the adjudicator on screen, refusing to lift a contradiction | the refusal is an evidence node, visible in the record |
| 5 | "this is RAG with hashes" | citations against documents, like everyone else | three classes of disagreement, named differently | zero margin, out of band, and a check we say we do not trust - on one real sheet |
| 6 | "they did not build it in six days" | 700 tests and a CAD kernel appearing from nowhere | disclosure, a dated base commit, a named increment | `PROVENANCE.md` + `git log` |
| 7 | "the ruling is arbitrary" | a disposition with no rule behind it | the truth table, published | `docs/engineering/decision-records.md` section "The truth table" |
| 8 | "my seat was not registered" | 250 seats, first-come | register today | the confirmation email |
| 9 | "I could not find the answer" | a 3,000-line repo, no entry point | the 60-second block at the top of `README.md` | three commands, one page |
| 10 | "the deck was a wall of text" | 40 slides, 9 minutes | eight slides, 4:40, rehearsed | a stopwatch, twice |
| 11 | "SOLIDWORKS was not installed" | the live CAD beat dies on the judge laptop | the clip is recorded, the live path is Python | the script never requires SW |
| 12 | "the licensed drawings are in their repo" | `_sources/` pushed | excluded, named in `PROVENANCE.md` | `git ls-files _sources` is empty |
| 13 | "they submitted at 23:50" | a flaky upload, a truncated file | submit at T-24h | upload timestamp |
| 14 | "a rehearsal was presented as a result" | synthetic outcomes in the demo | the label is in the artefact, not the narration | the page says "rehearsal - not a result" |

## Preflight, measured today (2026-11-25)

`py tools/preflight.py` runs ten checks against the judged path - no network, no third-party imports, a cp1252
console, a path with spaces, a tampered record - and fails loudly. This is the current run, not a hope:

```
  PASS  python               3.14.7
  PASS  stdlib only          7 modules, standard library only
  PASS  offline              ruled with sockets disabled
  PASS  deterministic ids    d-db57a72b2cd5
  PASS  spaces in paths      append and verify under a path with spaces
  PASS  cp1252 console       decide --json prints the diameter sign on a cp1252 console
  PASS  refuses blank actor  a ruling with no named actor is refused (exit 2)
  PASS  tamper is caught     one edited character is caught and named (exit 4)
  PASS  no stray writes      nothing written outside the named store
  PASS  utf-8 record         unicode survives the record file

  10/10 checks passed
```

It earned its keep on the first run: `py -m astracad_decide` does **not** import on a clean interpreter, because
the package lives under `src/` and nothing puts it on the path. That is the exact failure a judge would have hit
at 0:00 of the demo. The documented entry point must therefore be `py tools/run_demo.py`, which inserts `src/`
itself - not `py -m astracad_decide`. Preflight now pins that.

## Where the next point is cheapest

| Criterion | Weight | Your expected | Cheapest remaining point | Cost |
|---|---|---|---|---|
| Technical | 25 | high - a module, a chain, replay, refusals | replay (`Task 5`): "show me it was not hand-written" | 1 day |
| Innovation | 20 | high on concept, unproven in the room | the AI refusal beat, on screen, at 4:00 | 2 hours |
| Problem solving | 20 | strong - two real defects on a real sheet | the cost of a bad release, one sentence with a number | 1 hour |
| UX | 15 | weak today - a terminal and a generated page | the record page in the workspace (`Task 6`) | 1.5 days |
| Scalability | 10 | medium - honest, but unaddressed | one slide: JSONL to SQLite at 10k, and what breaks first | 30 minutes |
| Presentation | 10 | unknown until rehearsed | the 4:40 script, twice, offline | 3 hours |

The UX point is the only 15 that is currently unearned, and it is also the only one a judge forms an opinion on in
the first thirty seconds. That is why Task 6 comes before polish anywhere else.

## The first thirty seconds

A judge reviewing fifteen entries forms the first impression from four things, in this order: the submission
title, the one-line summary, the top of the repository, and the first frame of the video. Fix those before any code.

- **Title:** `ASTraCAD Omega - TrustChain: the decision engine that can be audited`.
- **One line:** "It rules on an inspection plan, writes down the evidence for and against, refuses to release on a
  contradiction - and learns which source was wrong last time."
- **Repo top:** the 60-second block, three commands, one screenshot of the record page, then `PROVENANCE.md`.
- **Video, frame 0:** the drawing with `R0.09` circled and `9.697655` beside it. No logo, no title card.

## The five questions that hurt

1. **"Did the AI make the decision?"** - No. It proposes, tagged, at weight 0.40, and a rule above every weight
   stops it lifting a blocking contradiction. Here is the refusal in the record.
2. **"What is your ground truth?"** - The geometry kernel and the measured value. Every other TrustChain entry
   compares documents with documents; that is why this one found two defects.
3. **"Why are nine of twelve rows not judgeable?"** - Because they carry no limit. A checker that passes them is
   lying; the plan says so and lists what each absence blocks.
4. **"Is that data real?"** - The plan is a real SOLIDWORKS sheet. The outcomes in the demo are labelled ground
   truth from synthetic fixtures, and the page says so.
5. **"How do I know the record was not edited?"** - Edit one field and the record names the field. Then say the
   honest limit: a whole-file rewrite verifies, which is why signing exists for dossiers.

## The clock

| When | What | Done means |
|---|---|---|
| T-24h | submit the working build | upload finished, hash of the zip recorded |
| T-6h | rehearse on a cold account, offline | `tools/preflight.py` prints 10/10 |
| T-1h | read the deck aloud once | under 4:40, no slide skipped |
| T-0 | nothing | no edits after T-24h, whatever they are |

## What this document will not do

It will not tell you a win is guaranteed, and it will not invent a number to make the entry look better. Those two
are the same mistake: the entry is about evidence, and an entry that lies about its evidence loses the only
argument it has.