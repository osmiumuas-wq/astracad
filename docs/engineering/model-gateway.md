# The local model as an optional planner (2026-10-18)

ASTraCAD's own interpreter reads a fixed set of phrasings exactly and never guesses. For a part those phrasings do
not cover, the user can ask **the language model running on this PC** - Qwen3.8-Flash-Next (the Coder build, IQ1_M),
served by **Strata** - for a plan. The model writes a typed part spec - never code - and ASTraCAD decides what that
plan is worth before anything can run. Nothing leaves the computer. (An earlier Gemini integration was removed at the
user's request; there is no cloud provider in ASTraCAD.)

## Setting it up

Start Strata the way you usually do - on this PC its launcher for the Coder model is `run-coder-iq1_m.bat` in the
Strata folder (`C:\New folder (3)\strata`). It serves an OpenAI-compatible API on `http://127.0.0.1:8080/v1`, which
ASTraCAD uses with no further setup. Optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `ASTRACAD_MODEL_PROVIDER` | `local` | `off` switches the planner off |
| `ASTRACAD_MODEL_URL` | `http://127.0.0.1:8080/v1` | the API's base URL - Strata, or any other OpenAI-compatible local server (llama.cpp, LM Studio, ...) |
| `ASTRACAD_MODEL_NAME` | `qwen3.8-flash-next-coder-iq1_m` | the model name sent (Strata accepts any) |
| `ASTRACAD_MODEL_THINKING` | `low` | `none` / `low` / `medium` / `high` (Strata's `reasoning_effort`); the thinking is capped at 2,500 tokens so the answer fits the 8K context |
| `ASTRACAD_MODEL_TIMEOUT_S` | `900` | seconds to wait for an answer |
| `ASTRACAD_MODEL_API_KEY` | - | only when the local server was started with an API key; sent as a header, never stored |
| `ASTRACAD_MODEL_ALLOW_REMOTE` | - | `1` allows an address that is not this PC - the request then leaves the computer |

**GPU memory (this laptop: RTX 4070 Laptop GPU, 8 GB).** The model and SOLIDWORKS do not fit on the card together.
On 2026-10-18 Strata stopped at start-up while SOLIDWORKS was open - even minimized with no document, SOLIDWORKS held
about 5 GB: "no VRAM is left for the expert cache ... about 994 MiB more than this card has free" (a lighter test
configuration without the speculative-decoding draft layer and images was still 907 MiB short). With SOLIDWORKS
exited, the model loaded in about 24 s (7.5 GB of the card in use). So: exit SOLIDWORKS, start Strata, ask; then stop
Strata, start SOLIDWORKS and press **Build in SolidWorks** on the same page - the page keeps the plan, and the build
reads the stored plan; it never needs the model.

## Using it

- Workspace: **Text to CAD** -> type the request -> **Ask local model**. The chip next to it says whether the model
  answers (click it to check again). The plan is shown like any interpretation: a preview, every value with its
  source, and the decisions it needs. Build in SolidWorks builds exactly that plan.
- Command line: `py -m astracad_cad build "<request>" --planner local --out OUT --authorize KM [--accept-proposals]`
  (`interpret ... --planner local` shows the plan and builds nothing).

## What is sent, and where

Only the request text and ASTraCAD's fixed planning instructions (`astracad_core.model_planner.INSTRUCTIONS`; the whole
prompt is about 1,180 tokens with the model's tokenizer), to the model server on this PC. No drawing, model, file, path or project data.
Nothing is sent unless the user presses Ask local model (or passes `--planner local`).

- Another address than this PC (`localhost`, `127.0.0.1`, `::1`) is refused before anything is sent, unless the user
  sets `ASTRACAD_MODEL_ALLOW_REMOTE=1`; connections are direct - a system proxy never sees the request.
- The request asks for no tools (`"tool_choice": "none"`); Strata offers its MCP tools only to requests that opt in
  with `"strata_mcp": true`, which ASTraCAD never sends. The model can only answer, not act.
- It asks for one JSON object (`"response_format": {"type": "json_object"}`): Strata checks the answer is one JSON
  object and otherwise refuses it (HTTP 502), which ASTraCAD reports.
- The full exchange - instructions, request, answer, the model's separate thinking text and the token counts - is
  kept under the run folder's `model_plans/<plan id>.json`.

## How the plan is checked

| Check | Rule |
|---|---|
| Form | One JSON object; anything else is refused ("not a usable JSON plan") |
| Operations | Only the CAD runtime's operations, only their argument names, numbers in range, references to earlier features, sketches consumed in order; anything else refused with the reason |
| Stated values | A number the model says came from the request counts as stated only when its quoted words are in the request and contain that number (units converted) - checked by ASTraCAD |
| Every number | Every number in the spec must be accounted for in the model's list of values; an unexplained number stops the plan |
| The model's choices | Positions, derived sizes, assumptions: shown as **Proposed**; the plan does not run until the user accepts them, for that build |
| Questions | When the model says a size is missing, its questions are shown and nothing runs |
| Same plan | The build re-reads the stored exchange, checks its hash (the plan id) and checks the answer again - it never asks the model a second time |

Then the usual: one authorised build into a new folder, SOLIDWORKS kernel measurements against the runtime's own
expectations (derived from the spec, not from the model), and an independent re-read of the exported STEP.

## What was verified (2026-10-18)

- 32 tests (`tests/test_model_planner.py`, `tests/test_web_model.py`): the plan checks; the client against a stand-in
  server on 127.0.0.1 (the request body: messages, no tools, JSON object, thinking settings; `<think>` text removed
  from the answer; errors 409 / 502 / context full / empty; a server that is not running; other addresses refused
  with no request sent; proxies ignored; an API key only as a header); the workspace routes; the live plan below
  that was wrong.
- Against **Strata's own server** (its 0.1.40 server code with the scripted `mock` engine and the Coder pack's
  tokenizer): ASTraCAD's request was accepted, the thinking came back apart from the answer, a non-JSON answer was
  refused by Strata's check and reported; the CLI and the workspace showed the checked plan.
- **Live, with the real model** (Strata 0.1.40, the user's `strata-coder-iq1_m.json`, Qwen3.8-Flash-Next Coder
  IQ1_M, thinking low; exchanges kept in `_out/qwen_live_20261018/`):

  | Request | Time | Tokens (prompt + answer incl. thinking) | What ASTraCAD made of it |
  |---|---|---|---|
  | SolidWorks L-shaped bracket, 80 x 60 mm outline with 10 mm legs, 8 mm thick, export STEP - first instructions | 88 s | 1,101 + 1,265 | Wrong part: the outline as a `sketch.profile` (the runtime only revolves those) with the upright leg 70 mm wide (its "inner corner x = 70, derived 80 - 10"). The 70 was held as Proposed, so it could not build unaccepted; now also refused outright ("feature.extrude cannot use a sketch.profile") |
  | the same, instructions sharpened (outlines are `sketch.loops`; walk every edge against the request) | 78 s | 1,179 + 1,347 | Correct L (both legs 10 mm) as one loop, all four numbers quoted from the request and confirmed: ready to build |
  | SolidWorks plate 100 x 50 x 6 mm with a 30 mm long, 10 mm wide slot with round ends in the middle, export STEP | 183 s | 1,188 + 3,011 | Correct: outer loop + a slot loop of two lines and two arcs (x 35..65, y 20..30); its 7 derived values (radius 5, arc centres 40 / 60, ...) shown as Proposed, waiting for acceptance |
  | SolidWorks L-shaped bracket with 10 mm legs, export STEP | 25 s | 1,164 + 231 | `need_info`: asked for the outer sizes, what "10 mm legs" means and the thickness; nothing to build |

  Not done live: building one of these plans in SOLIDWORKS (the build path is the same as for every plan and is
  covered by the workspace tests and earlier builds; this run was about the planning).

## Limits

- A plan is only as good as the model's reading; the checks catch plans that are malformed, that misquote the
  request or that use numbers they do not explain - not a plan that is well-formed but means a different part.
  That is why the model's own choices need acceptance and the preview is shown first.
- IQ1_M is an extremely compressed build of the model (about 1.75 bits per weight): expect weaker instruction
  following than the full model - live, its first L-bracket had the wrong leg width while its own thinking said the
  right one. Every plan is checked; a refused plan says why; a derived value is never built unaccepted.
- A plan takes about 25 s to 3 min on this laptop (about 17 tokens/s on the longer answers, thinking low).
- The model can only use the operations listed; it cannot invent new CAD features. Drawings are not sent to it.
- 8,192-token context: about 1,200 for the prompt, up to 2,500 for thinking; an answer that does not fit is
  refused with that reason.
- Any program listening on the configured local port receives the request text.
