# ASTraCAD workspace — design system (MASTER)

Derived with the *UI UX Pro Max* method (github.com/nextlevelbuilder/ui-ux-pro-max-skill, read
2026-10-17; its rules were applied, none of its code or assets were copied). Its catalogue has no
row for manufacturing quality inspection, so the nearest rows were combined and the choice is
recorded here rather than presented as the catalogue's answer.

| Source row (products.csv) | What was taken |
|---|---|
| Analytics Dashboard (7) | Data-Dense Dashboard style; "clarity over aesthetics"; colour coded by priority |
| Inventory & Stock Management (102) | Functional neutrals + traffic-light status colours; audit log as a first-class view |
| Status Page / Incident Mgmt (166) | Severity badges, timeline for history and audit |
| Construction / Architecture (51) | Blueprint blue accent; drawing-first ("blueprint aesthetic") |
| Developer Tool / IDE (81) | Dark theme as an equal citizen; keyboard shortcuts; command palette |

Style: **Data-Dense Dashboard + Minimalism & Swiss Style**. 12–14 px data text, 8 px gaps,
12 px padding, sticky table headers, sortable tables, working filters, loading states, export.

## Tokens (`:root` in `src/astracad_web/index.html`)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | #f3f4f6 | #0b0d11 | app canvas |
| `--surface` | #ffffff | #12151b | panels |
| `--surface-2` | #f8f9fb | #181c23 | headers, hovers |
| `--line` | #e2e5ea | #252b35 | 1 px rules |
| `--ink` | #0e1420 | #e9edf3 | primary text (≥ 15:1 light, ≥ 13:1 dark) |
| `--muted` | #5b6472 | #949eac | secondary text (≥ 4.6:1 on surface in both) |
| `--accent` | #2456e6 | #7b9bff | blueprint blue: selection, primary action |
| `--ok` | #0f7a4f | #4fd39a | approved / passed |
| `--warn` | #a35a00 | #f3b453 | needs review |
| `--bad` | #c62038 | #ff7a8c | blocking / failed |
| `--info` | #3a5a8c | #9fb4d8 | detected, not yet reviewed |

The drawing sheet itself is always white paper in both themes: it is the engineering artefact.

## Type

System stacks only (the workspace is offline and its CSP forbids remote fonts):
`Inter, "Segoe UI Variable Text", "Segoe UI", system-ui` and
`"JetBrains Mono", "Cascadia Code", Consolas, ui-monospace` for callouts, limits and hashes.
Numbers use `font-variant-numeric: tabular-nums`. Scale: 11 / 12 / 13 / 14 / 16 / 20 / 24.

## Rules enforced (skill checklist)

- Status never by colour alone: every state has an icon **and** a word.
- SVG icons only (Lucide-style strokes drawn inline), no emoji.
- Visible `:focus-visible` rings; every icon-only button has `aria-label`.
- `prefers-reduced-motion` turns camera animation and transitions off.
- 150–250 ms transitions on opacity/transform only.
- Labels always visible (placeholders are examples, not labels).
- Touch targets ≥ 44 px under `pointer: coarse`; layout checked at 375 / 768 / 1024 / 1440.
- No gradients-as-decoration, no purple/pink "AI" gradients, no glassmorphism over data.

## Keyboard

`Ctrl/⌘ K` command palette · `/` search characteristics · `J`/`K` or `↓`/`↑` next/previous ·
`A` approve · `R` reject · `C` correct · `F` fit drawing · `+`/`-` zoom · `Esc` close.
