# Submission fields, ready to paste (2026-11-25)

The registration form is four steps: **Basic** (full name, email, 10-digit mobile) -> **Academic** -> **Track** ->
**Fee** (Rs 149). The fields below are the ones that describe the project; the first two are yours alone.

## Values to paste

| field | value |
|---|---|
| Project title | `ASTraCAD` - a self-auditing release decision engine |
| Track | AI |
| Problem statement | SNH-AI-05 - TrustChain AI: Self-Auditing AI Decision Engine |
| Team | Individual |
| Repository | `https://github.com/<you>/<repo>` (public) |
| Demo video | `https://...` (recorded on 5-6 December) |

## One line (25 words)

An inspection sheet is held, not passed: every callout is judged against the drawn geometry, every gap is named,
and a person signs what the engine could not prove.

## Short description (about 70 words)

ASTraCAD reads a drawing, compares each stated dimension with the geometry actually drawn, and issues a release
decision that names what it cannot judge. Ten of the twelve callouts on our test sheet carry no limit a machine can
pass, so the sheet is held and the gap is listed. Every claim is a hashed evidence node; a person must approve; the
engine learns which source was wrong last time.

## Long description (about 190 words)

Most inspection checkers compare numbers to numbers: a callout against a tolerance. They pass a sheet that is
complete on paper and unjudgeable in fact. ASTraCAD compares the *part* to the claim - the stated dimension against
the geometry drawn in the same file - and that gives it one source of truth that does not depend on what someone
typed.

On a real production drawing it rules HOLD: one callout (R0.09) disagrees with a measured 9.697655, ten more carry
no limit and are reported as not judgeable, and fourteen absences are listed with what each one blocks. Nothing is
averaged and nothing is guessed. Each finding is a SHA-256 hashed evidence node with an author, a stance and a
weight; the ruling states the weakest decisive weight and withholds a calibrated probability until five outcomes
exist. An agent layer may only propose, at weight 0.40, below every rule that can refuse.

The engine audits its own checks: a false positive found in our own cross-check (a value exactly on the printed
boundary, failed by four times ten to the minus nineteen of floating point) is fixed in the commit history before
the event. AI proposes, evidence decides, a person approves.

## Tech stack (one line, for the form)

Python 3.14 (standard library only), append-only JSONL evidence ledger, SHA-256 provenance, Ed25519 signed
dossiers, EZ-DXF geometry, offline model gateway with record/replay, self-contained HTML record page.

## Disclosure sentence (paste wherever the form asks about prior work)

ASTraCAD is my own pre-existing project: the CAD, inspection and manufacturing modules predate SNH'26 and are
dated in the git history. The decision layer (astracad_decide) and the agent layer are written during the build
window; the pre-existing parts are named in PROVENANCE.md rather than presented as new.

## What the judges will be shown

1. `_out/decide_demo/index.html` - the signed record for one real sheet.
2. `_out/decide_demo_before_c1/` - the same record before the audit fix (the before/after beat).
3. The five-minute video, if the venue network fails.