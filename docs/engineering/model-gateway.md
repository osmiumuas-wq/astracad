# Gemini as an optional planner (2026-10-18)

ASTraCAD's own interpreter reads a fixed set of phrasings exactly and never guesses. For a part those phrasings do
not cover, the user can ask **Google Gemini** for a plan. Gemini writes a typed part spec - never code - and
ASTraCAD decides what that plan is worth before anything can run.

## Setting it up (once)

**With your Google AI Pro plan** (the plan's Gemini CLI quota is used; no key is stored by ASTraCAD):

    npm install -g @google/gemini-cli      # Google's CLI, Apache-2.0, ~99 MB
    gemini                                  # once: choose "Login with Google", sign in, then exit

**Or with an API key** from Google AI Studio (billed / limited separately from the AI Pro plan):

    setx GEMINI_API_KEY <your key>          # read from the environment, never written to a file by ASTraCAD

Optional: `ASTRACAD_MODEL_PROVIDER` = `gemini-cli` | `gemini-api` | `off` (default: the CLI when installed, else the
API when a key is set); `ASTRACAD_GEMINI_MODEL` (CLI default `pro`, API default `gemini-2.5-pro`).

## Using it

- Workspace: **Text to CAD** -> type the request -> **Ask Gemini**. The plan is shown like any interpretation:
  a preview, every value with its source, and the decisions it needs. Build in SolidWorks builds exactly that plan.
- Command line: `py -m astracad_cad build "<request>" --planner gemini --out OUT --authorize KM [--accept-proposals]`
  (`interpret ... --planner gemini` shows the plan and builds nothing).

## What leaves the PC

Only the request text and ASTraCAD's fixed planning instructions (in `astracad_core.model_planner.INSTRUCTIONS`).
No drawing, model, file, path or project data. Nothing is sent unless the user presses Ask Gemini (or passes
`--planner gemini`). The full exchange - prompt and answer - is kept under the run folder's `model_plans/<plan id>.json`.

## How the plan is checked

| Check | Rule |
|---|---|
| Form | One JSON object; anything else is refused ("not a usable JSON plan") |
| Operations | Only the CAD runtime's operations, only their argument names, numbers in range, references to earlier features, sketches consumed in order; anything else refused with the reason |
| Stated values | A number Gemini says came from the request counts as stated only when its quoted words are in the request and contain that number (units converted) - checked by ASTraCAD |
| Every number | Every number in the spec must be accounted for in Gemini's list of values; an unexplained number stops the plan |
| Gemini's choices | Positions, derived sizes, assumptions: shown as **Proposed**; the plan does not run until the user accepts them, for that build |
| Questions | When Gemini says a size is missing, its questions are shown and nothing runs |
| Same plan | The build re-reads the stored exchange, checks its hash (the plan id) and checks the answer again - it never asks Gemini a second time |

Then the usual: one authorised build into a new folder, SOLIDWORKS kernel measurements against the runtime's own
expectations (derived from the spec, not from Gemini), and an independent re-read of the exported STEP.

## Safety of the CLI route

The request travels on standard input, never on the command line (a request full of shell characters and
`--yolo` stays data - covered by a test); the CLI runs in an empty scratch folder with `--approval-mode plan`
(read-only) when the installed CLI supports it, and `--output-format json`. The API route sends the key as the
`x-goog-api-key` header, never in the URL (tested).

## Limits

- A plan is only as good as Gemini's reading; the checks catch plans that are malformed, that misquote the
  request or that use numbers they do not explain - not a plan that is well-formed but means a different part.
  That is why Gemini's own choices need acceptance and the preview is shown first.
- Gemini can only use the operations listed; it cannot invent new CAD features. Drawings are not sent to it.
- The live round trip with a signed-in Gemini CLI has not been run yet on this PC (the CLI was not installed).
