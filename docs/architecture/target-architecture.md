# ASTraCAD — target architecture

Mission (constitution §I): *build a reliable, extensible, verified AI engineering workspace by
integrating the best compatible existing software, models, engineering libraries, and CAD
backends.* This document is the shape that work is aiming at, contrasted against
`docs/architecture/current-state.md`. It names what already exists and what is a seam waiting to
be filled — it does not describe absent code as if it were present.

## 1. Layers

```
                 ┌──────────────────────────────────────────────┐
   interaction   │ CLI (exists)  ·  correction UI (absent)      │
                 ├──────────────────────────────────────────────┤
   deliverable   │ PDF · XLSX · JSON · PNG        (exist)       │
                 ├──────────────────────────────────────────────┤
   verification  │ plan checks · release gate · reopen-and-read │
                 │ (exist)  ·  labelled regression metrics      │
                 ├──────────────────────────────────────────────┤
   engineering   │ callout parser · limits · balloons · coverage│
                 │ (exist)  ·  GD&T semantics · ISO 286 ·       │
                 │ thread data (absent)                          │
                 ├──────────────────────────────────────────────┤
   interpretation│ dimension/text extraction        (exists)    │
                 │  · OCR · PDF/raster ingest (absent)          │
                 ├──────────────────────────────────────────────┤
   ingestion     │ DXF reader + entity census (exists)          │
                 │  · CAD backends: SolidWorks `mac` adapter,   │
                 │    FreeCAD/OCC, STEP (absent)                │
                 └──────────────────────────────────────────────┘
```

The verification layer is not a testing afterthought bolted on the side: it is a peer of the
engineering layer, and nothing leaves the system without crossing it. This mirrors
`MecAgent.Core/Verification.cs`, where a measured state is "produced only by measurement, never
by the executor's own claims".

## 2. Invariants (each is enforced somewhere in code today)

1. **Source linkage.** Every characteristic carries
   `document / entity_handle / entity_type / layer / page / document SHA-256`
   (`plan.py: rows_link_to_source_drawing`). A row without a source link is reviewer-stated and
   labelled as such — never presented as extracted.
2. **Nothing is invented.** Absent tolerance ⇒ `NoTolerance` ⇒ no limits ⇒ the row blocks. A fit
   class ⇒ recognised, unresolved, blocked. Ambiguous notation ⇒ escalated with the reason.
   Enforced in `limits.resolve_limits`, `callouts._classify_words`, `plan.build_plan`.
3. **Independent expectation.** Values are checked against a *different* route than the one that
   produced them: written callout vs drawn definition points (`geometry_cross_check`), stored
   limits vs recomputed limits plus band width (`limits_independently_recomputed`), exported
   artefact vs in-memory plan (`validate.py`, different libraries).
4. **Three-valued outcomes.** `passed / failed / not_verifiable`. "Not checked" never renders as
   a tick, and a NOT_VERIFIABLE check can be *accepted as an exception by a named reviewer* —
   which reclassifies it, it does not pass it (`ACCEPTED_EXCEPTION_CHECKS`, `gate_statement()`).
5. **Explicit units.** `$INSUNITS = 0` raises `UnitError`. There is no mm default anywhere.
6. **Immutable raw text.** `raw_callout` is never rewritten; normalisation and interpretation are
   separate fields, so a reviewer can always see what the drawing actually said.
7. **Determinism.** Same input + same code ⇒ same cid ordering, same balloon numbering, same
   table. No randomness, no model call in the numeric path.
8. **Honest gate.** The workflow must not claim completeness while required characteristics are
   unresolved: `approvable` is false if anything blocks, anything is unplaced, or any check is
   neither passed nor explicitly accepted.
9. **Artefacts cannot self-attest.** Verification and provenance JSON are written *last*, because
   an artefact cannot carry its own hash.

## 3. Extension seams (where the next capability goes, without re-plumbing)

| Seam | Existing shape | Planned filler |
|---|---|---|
| In-route | `ingest/dxf.py:read_dxf(path, declared_unit, revision) -> DrawingBundle` | `ingest/pdf.py`, `ingest/raster.py` returning the **same** `DrawingBundle`, with `provenance.state = extracted` only where text was truly read; OCR results enter as `ai_proposed`/`unresolved`, never as `parsed` |
| Backend | none yet | `backend/mac_adapter.py` calling the .NET `mac` CLI, or STEP via OCC — must return *measured* geometry (per project memory, two SolidWorks properties silently misreport on this install, so re-read, don't trust) |
| Notation | `callouts.py` table-driven `_classify_words`, `NON_TOLERANCE_WORDS`, `_THREAD`, `_FIT_CODE` | ISO 286 grade tables, thread data, GD&T frame grammar — each as data, each refusing to resolve when the table is absent |
| Correction | `plan.apply_overrides(bundle, path)` with attributed history | a UI that edits the same override document; no second correction channel may exist |
| Verification | `plan.checks`, `validate.verify_artifacts` | add checks, never remove a failing one to get green (§XVI) |
| Cross-runtime contract | JSON: `{stem}.plan.json`, `.provenance.json`, `.verification.json`, `*.report.json` in the sibling project | keep JSON as the only shared format between the Python and .NET sides |

## 4. What the target deliberately keeps out

- No machine-control, no actuation, no "write to the shop floor" path (§XV: human authorisation
  and safety controls are prerequisites, not features).
- No model call inside the numeric path. Proposals may enter only as `ai_proposed` and must be
  promoted by a check or a named reviewer before they affect a limit.
- No upload of drawing content to external providers without explicit authorisation and policy
  (§XVII); the local route is the default.
- No confidence scores replacing provenance states, and no "auto-accept" of ambiguous notation.

## 5. First acceptance target

Sakthi Hackathon 2.0, PS-012 — *Automated Inspection Layout Generation from 2D Engineering
Drawings*. Steps 1–13 of constitution §XX are implemented and measured for the DXF route
(current-state §3). The remaining distance to a strong entry is: GD&T frame and datum support,
a real (non-synthetic) labelled drawing set, and an interactive
correction path — see `docs/development/implementation-roadmap.md`.


---

## Module map as built (2026-10-17)

```
request text ──> astracad_core.requests (deterministic; facts carry USER_SPECIFIED/CALCULATED/AI_PROPOSED)
                     │ spec + expectations + missing[]
                     v
astracad_cad.orchestrator ── permission gate (risk class, named authorization, output dirs)
     │            └── astracad_core.tasks (SQLite: state machine + event log)
     v
adapters: solidworks ──subprocess argv──> mac.exe (C#, COM) ──> SOLIDWORKS 2026
          creo / fusion / autocad: discovery only
     │
     v
verification: kernel checks (from mac report) + astracad_cad.step_check (independent STEP re-read)

astracad_inspect (DXF → plan → PDF/XLSX, revision diff)    astracad_tol (stacks, fits, selective)
```
