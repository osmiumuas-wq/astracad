# Demo video script (SNH'26) — 3 minutes, no narration theatre

Judges score Presentation & Demo at 10%. A video is the only artefact that survives a judge who clicks nothing, so
it has to be honest, specific, and short. Target **2:50–3:10**.

## Before you record

- Screen: 1280×800 or larger, 100% browser zoom, dark mode off (light reads better on projectors).
- Tabs open, in order: (1) `…/astracad/`, (2) `…/record.html`, (3) the GitHub README, (4) a terminal at the repo root.
- Close every notification, chat window and unrelated tab. One stray WhatsApp pop-up costs the whole take's credibility.
- Speak from bullets, not a script. Re-record rather than apologise mid-take.

## Shot list

| # | Time | On screen | Say |
| --- | --- | --- | --- |
| 1 | 0:00–0:20 | Landing page, H1 fills the frame | "Inspection plans get released on documents that disagree. This engine decides which disagreement stops a release — and it shows its work." |
| 2 | 0:20–0:50 | `record.html` top: the ruling banner, HOLD | "One decision, one ruling: HOLD. Not a score, not a summary. A person has to arbitrate, and the record says why." |
| 3 | 0:50–1:25 | Scroll to the callouts table | "Eleven of these twelve callouts are not judgeable — there are no definite limits, so a measurement could be recorded but never judged. That is a finding, not a blank." |
| 4 | 1:25–1:55 | The disagreement / two-source block | "The drawing and an independent parse disagree on one characteristic. The engine does not average them. It withholds the release and hands the arbitration to a human." |
| 5 | 1:55–2:20 | Confidence floor + source weights | "Confidence is the weakest premise, not the average of the strong ones. And each source carries a calibrated weight that only moves after five outcomes — before that, it says so." |
| 6 | 2:20–2:45 | Terminal: tamper the JSONL, run the verifier | "Now the part that matters. I edit one line of the chain. The verifier refuses the record — exit 4. The audit trail is not decorative." |
| 7 | 2:45–3:05 | README / repo tree | "One command reproduces all of it: standard library, no model API key, no CAD install. The code is public, the record is live, and the limits are written down where you can read them." |

## The tamper shot, exactly

```powershell
# terminal, repo root, after the demo has written _out/decide_demo/
$env:PYTHONPATH = "src"
py -m astracad_decide verify _out/decide_demo/decisions.jsonl    # exits 0
# edit one byte of one line in that file, then:
py -m astracad_decide verify _out/decide_demo/decisions.jsonl    # exits 4, names the broken record
```

Rehearse the tamper line until it is one clean keystroke sequence — fumbling on camera reads as a broken product.

## What not to do

- Do not claim a win, a benchmark, a client or production use. The video's credibility *is* its honesty.
- Do not show the five rehearsal outcomes as if they were real measurements; the page labels them, so say the word
  "rehearsal" out loud when they appear.
- Do not speed-ramble the architecture. Judges reward a clear wedge over a feature list.
- Do not put the video anywhere that needs a login. Unlisted YouTube is the safe default.

## Publish

- Upload **unlisted**, title: `ASTraCAD Ω — self-auditing decision engine (SNH'26 AI track)`.
- Description: one line, the repo URL, the live page URL, and the registration ID.
- Paste the URL into the submission form and into `submission-form-text.md`.
- Verify it opens in a private window before you submit.
