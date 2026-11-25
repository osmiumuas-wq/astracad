"""Rehearse the decision loop on a real plan and write the page a reviewer reads.

    py tools/decide_demo.py _out/layout_after/acad_7550_021_s1_dxf/acad_7550_021_s1.plan.json

Writes into `_out/decide_demo/`: the decision store, the outcome ledger, the record as JSON, and
`index.html` - one page carrying the ruling, its evidence, what is missing, the chain, and the
effect of five recorded outcomes.

The outcomes are a REHEARSAL and the page says so in as many words: they are made up to show the
loop moving, not measurements of a part. The engine treats them exactly as it would real ones,
which is the point of rehearsing - and the reason a rehearsal may never be filed as a result.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from astracad_decide import engine, model, outcome, report
from astracad_decide.cli import utf8_console


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def badge(disposition: str) -> str:
    known = {"RELEASE": "ok", "HOLD": "hold", "REJECT": "bad", "NOT_EVALUABLE": "none"}
    return "<span class='" + "badge " + known.get(disposition, "none") + "'>" + esc(disposition) + "</span>"


def table(headers, rows) -> str:
    out = ["<table><thead><tr>"]
    out.extend("<th>" + esc(h) + "</th>" for h in headers)
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>" + "".join("<td>" + cell + "</td>" for cell in row) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def stat(label, value, note="") -> str:
    return ("<div class='stat'><b>" + esc(value) + "</b><span>" + esc(label) + "</span>"
            + ("<i>" + esc(note) + "</i>" if note else "") + "</div>")


def bars(prior: float, learned: float) -> str:
    """One track: the fill is the learned weight, the tick is the stated prior."""
    return ("<div class='track'><i style='width:" + str(round(learned * 100, 1)) + "%'></i>"
            + "<u style='left:" + str(round(prior * 100, 1)) + "%'></u></div>")


CSS = """
:root{--ok:#3ddc97;--hold:#f5b544;--bad:#ff6b6b;--none:#8a8a8a}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;font-family:var(--font-sans,system-ui,sans-serif);color:var(--foreground,#eee);
font-size:14px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{display:flex;flex-direction:column;gap:30px;padding-bottom:6px}
code,.hash,.id{font-family:var(--font-mono,ui-monospace,monospace);font-variant-ligatures:none}
.eyebrow{font-family:var(--font-mono,monospace);font-size:10.5px;letter-spacing:.18em;
text-transform:uppercase;color:var(--muted-foreground,#8a8a8a)}
h1{font-size:clamp(24px,4vw,38px);letter-spacing:-.035em;line-height:1.05;margin:8px 0 0;font-weight:800}
h1 .thin{display:block;font-size:.42em;font-weight:400;letter-spacing:0;
color:var(--muted-foreground,#9a9a9a);margin-bottom:9px}
h2{font-size:14px;margin:0 0 2px;font-weight:700;display:flex;gap:10px;align-items:baseline}
h2 .n{font-family:var(--font-mono,monospace);font-size:10px;letter-spacing:.14em;color:var(--accent,#7cf)}
section{display:flex;flex-direction:column;gap:10px}
.lede{color:var(--muted-foreground,#9a9a9a);max-width:78ch;margin:0}
.card{border:1px solid var(--border,#2a2a2a);border-radius:var(--radius,10px);padding:18px 20px}
.card.ruling{border-color:color-mix(in srgb,var(--accent,#7cf) 55%,var(--border,#2a2a2a));
background:linear-gradient(180deg,color-mix(in srgb,var(--accent,#7cf) 7%,transparent),transparent 75%)}
.card.ruling .top{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between}
.card.ruling h3{margin:12px 0 0;font-size:clamp(18px,2.2vw,23px);letter-spacing:-.02em;line-height:1.2}
.badge{font-family:var(--font-mono,monospace);font-size:11px;letter-spacing:.16em;padding:4px 9px;
border-radius:5px;border:1px solid currentColor}
.badge.ok{color:var(--ok)}.badge.hold{color:var(--hold)}
.badge.bad{color:var(--bad)}.badge.none{color:var(--none)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:1px;
background:var(--border,#2a2a2a);border:1px solid var(--border,#2a2a2a);
border-radius:var(--radius,10px);overflow:hidden}
.stat{background:var(--background,transparent);padding:11px 13px}
.stat b{display:block;font-family:var(--font-mono,monospace);font-size:18px;letter-spacing:-.02em}
.stat span{font-family:var(--font-mono,monospace);font-size:9.5px;letter-spacing:.11em;
text-transform:uppercase;color:var(--muted-foreground,#8a8a8a)}
.stat i{display:block;font-style:normal;font-size:10.5px;color:var(--muted-foreground,#7a7a7a);margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{text-align:left;font-family:var(--font-mono,monospace);font-size:9.5px;letter-spacing:.12em;
text-transform:uppercase;color:var(--muted-foreground,#8a8a8a);font-weight:600;padding:0 8px 6px;
border-bottom:1px solid var(--border,#2a2a2a);white-space:nowrap}
td{padding:7px 8px;border-bottom:1px solid color-mix(in srgb,var(--border,#2a2a2a) 55%,transparent);
vertical-align:top}
code{font-size:11.5px;color:var(--foreground,#ddd);
background:color-mix(in srgb,var(--foreground,#fff) 6%,transparent);padding:1px 4px;border-radius:3px}
.id{font-size:10.5px;color:var(--muted-foreground,#8a8a8a);white-space:nowrap}
.why{color:var(--muted-foreground,#a0a0a0)}
.tag{font-family:var(--font-mono,monospace);font-size:9.5px;letter-spacing:.1em;padding:1px 5px;
border:1px solid var(--border,#2a2a2a);border-radius:4px;white-space:nowrap}
.tag.blocking{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 55%,transparent)}
.tag.warning{color:var(--hold);border-color:color-mix(in srgb,var(--hold) 45%,transparent)}
.acc{color:var(--ok)}.hld{color:var(--hold)}.nj{color:var(--none)}
.note{font-size:12px;color:var(--muted-foreground,#8a8a8a);margin:0}
.pair{display:flex;flex-direction:column;gap:3px;min-width:70px}
.track u{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--foreground,#1b1b1b);
opacity:.45;text-decoration:none}
.track{height:5px;border-radius:3px;background:color-mix(in srgb,var(--foreground,#fff) 9%,transparent);
position:relative;overflow:hidden;width:100%}
.track i{position:absolute;inset:0 auto 0 0;display:block;border-radius:3px;
background:color-mix(in srgb,var(--foreground,#fff) 34%,transparent)}
.track.learned i{background:var(--accent,#7cf)}
.hash{font-size:10.5px;word-break:break-all;color:var(--muted-foreground,#8a8a8a)}
.rehearsal{border-color:color-mix(in srgb,var(--hold) 45%,var(--border,#2a2a2a))}
.rehearsal .eyebrow{color:var(--hold)}
ol,ul{margin:6px 0 0;padding-left:20px}
li{margin:3px 0}
.foot{display:flex;flex-direction:column;gap:6px;border-top:1px solid var(--border,#2a2a2a);padding-top:14px}
"""

# The page is read in two places: inside the workspace, where the editor injects its own theme
# tokens, and opened on its own, where there are none. These overrides keep it legible in both,
# and keep the semantic colours readable on a white page as well as a dark one.
CSS += """
:root{--ok:#1a8f63;--hold:#b4791b;--bad:#b93a3a;--none:#6b7280}
body{color:var(--foreground,#1b1b1b)}
.eyebrow,.stat span,.stat i,.id,.hash,.why,.note,h1 .thin{color:var(--muted-foreground,#5f5f5f)}
.card,.stats,.stat,.tag,.foot,.tag{border-color:var(--border,#dcdcdc)}
th{border-bottom-color:var(--border,#dcdcdc)}
td{border-bottom-color:color-mix(in srgb,var(--border,#dcdcdc) 75%,transparent)}
.card.ruling{border-color:color-mix(in srgb,var(--accent,#2563eb) 55%,var(--border,#dcdcdc));
background:linear-gradient(180deg,color-mix(in srgb,var(--accent,#2563eb) 6%,transparent),transparent 75%)}
.track{background:color-mix(in srgb,var(--foreground,#1b1b1b) 12%,transparent)}
.track i{background:var(--accent,#2563eb)}
.track.learned i{background:var(--accent,#2563eb)}
code{background:color-mix(in srgb,var(--foreground,#1b1b1b) 8%,transparent);color:var(--foreground,#1b1b1b)}
.rehearsal{border-color:color-mix(in srgb,var(--hold,#b4791b) 55%,var(--border,#dcdcdc))}
.rehearsal .eyebrow{color:var(--hold,#b4791b)}
.stats{background:var(--border,#e3e3e3);border-color:var(--border,#e3e3e3)}
.stat{background:var(--card,#f7f7f7)}
.card{background:transparent}
th,.tag,.note,.why,.id,.hash{color:var(--muted-foreground,#5f5f5f)}
.tag{border-color:var(--border,#dcdcdc)}
.foot{border-top-color:var(--border,#dcdcdc)}
"""


def build_page(d1: dict, d2: dict, kinds: list[dict], summary: dict, plan_path: str) -> str:
    subj = d1.get("subject", {})
    conf = d1.get("confidence", {})
    after = d2.get("confidence", {})
    items = d1.get("items", [])
    counts: dict[str, int] = {}
    for i in items:
        counts[i["disposition"]] = counts.get(i["disposition"], 0) + 1
    tone = {"ACCEPT": "acc", "HOLD": "hld", "NOT_JUDGEABLE": "nj", "REJECT": "bad"}

    parts = ["<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<title>Decision " + esc(d1["decision_id"]) + "</title><style>" + CSS + "</style>",
             "</head><body><div class='wrap'>"]

    parts.append("<div><div class='eyebrow'>ASTraCAD - decision record</div><h1>"
                 + esc(d1["decision_id"]) + "<span class='thin'>" + esc(subj.get("document"))
                 + " - revision " + esc(subj.get("revision") or "none")
                 + ("" if subj.get("revision_stated", True) else " (assumed, not printed)")
                 + " - "
                 + esc(subj.get("characteristics")) + " callouts</span></h1></div>")

    parts.append("<div class='card ruling'><div class='top'><div class='eyebrow'>Ruling</div>"
                 + badge(d1["disposition"]) + "</div><h3>" + esc(d1["rationale"]) + "</h3>"
                 "<p class='note'>Stated by " + esc(d1["actor"]) + " at " + esc(d1["created_utc"])
                 + ". The plan gate is " + ("open" if subj.get("approvable") else "closed")
                 + "; this record reads the same checks as evidence, not as a verdict.</p></div>")

    parts.append("<div class='stats'>" + stat("callouts", len(items))
                 + stat("accepted", counts.get("ACCEPT", 0))
                 + stat("not judgeable", counts.get("NOT_JUDGEABLE", 0))
                 + stat("contradictions", len(d1["contradictions"]))
                 + stat("absences", len(d1["unknowns"]))
                 + stat("evidence floor", conf.get("floor"))
                 + stat("calibrated", "withheld" if conf.get("calibrated") is None
                        else conf.get("calibrated")) + "</div>")

    parts.append("<section><h2><span class='n'>01</span>Every callout has a ruling, not a score</h2>"
                 "<p class='lede'>A row that cannot be judged says so. A row whose stated value"
                 " disagrees with the geometry on the sheet is held, never passed.</p>"
                 + table(["id", "callout", "ruling", "because"],
                         [["<span class='id'>" + esc(i["cid"]) + "</span>",
                           "<code>" + esc(i["callout"]) + "</code>",
                           "<span class='" + tone.get(i["disposition"], "") + "'>"
                           + esc(i["disposition"]) + "</span>",
                           "<span class='why'>" + esc(i["because"])
                           + "".join("<br><span class=why style=opacity:.7>" + esc(n)
                                     + "</span>" for n in (i.get("notes") or []))
                           + "</span>"] for i in items])
                 + "</section>")

    contras = d1["contradictions"]
    if contras:
        parts.append("<section><h2><span class='n'>02</span>What cannot both be true</h2>"
                     "<p class='lede'>Both sides are kept. Nothing is averaged, nothing is"
                     " resolved by the engine: a person arbitrates.</p>"
                     + table(["severity", "subject", "disagreement"],
                             [["<span class='tag " + esc(c["severity"]) + "'>" + esc(c["severity"])
                               + "</span>", "<span class='id'>" + esc(c["subject"]) + "</span>",
                               esc(c["message"])] for c in contras]) + "</section>")

    parts.append("<section><h2><span class='n'>03</span>What the source does not say</h2>"
                 "<p class='lede'>A plan that hides its gaps looks exactly like a complete one."
                 " These are the gaps, and what each one blocks.</p>"
                 + table(["code", "subject", "blocks", "consequence"],
                         [["<span class='id'>" + esc(u["code"]) + "</span>",
                           "<span class='id'>" + esc(u["subject"]) + "</span>",
                           "<span class='tag'>" + esc(u["blocks"]) + "</span>",
                           "<span class='why'>" + esc(u["message"]) + "</span>"]
                          for u in d1["unknowns"]]) + "</section>")

    rows = []
    for prior, got in kinds:
        rows.append(["<span class='id'>" + esc(got["kind"]) + "</span>",
                     bars(prior, got["mean"]),
                     "<span class='id'>" + str(got["mean"]) + "</span>",
                     "<span class='id'>" + str(got["n"]) + "</span>",
                     "<span class='tag'>" + ("learned" if got["used"] else "prior only") + "</span>"])
    parts.append("<section><h2><span class='n'>04</span>Confidence, and where it comes from</h2>"
                 "<p class='lede'>Two numbers, never one. The floor is the reliability of the"
                 " weakest piece of evidence the ruling stands on. The calibrated value is the"
                 " observed frequency of the ruling holding, withheld until five outcomes exist.</p>"
                 "<div class='card'><p class='note'><b>floor " + esc(conf.get("floor"))
                 + "</b>. " + esc(conf.get("note")) + "</p></div>"
                 + table(["evidence kind", "learned / prior", "weight", "n", "state"], rows)
                 + "<p class='note'>The bar is the learned weight; the tick is the stated prior."
                 " Nothing moves until three outcomes name that kind.</p>"
                 "</section>")

    parts.append("<section><h2><span class='n'>05</span>The smallest change that would move this"
                 " ruling</h2><ol>" + "".join("<li>" + esc(a) + "</li>" for a in d1["next_actions"])
                 + "</ol><p class='note'>Rulings not taken: "
                 + "; ".join("<b>" + esc(a["disposition"]) + "</b> - " + esc(a["why_not"])
                             for a in d1["alternatives"]) + "</p></section>")

    nodes = d1["evidence"]
    parts.append("<section><h2><span class='n'>06</span>Evidence (" + str(len(nodes)) + " nodes)</h2>"
                 "<p class='lede'>Each node is one claim with an author, a stance and a weight."
                 " This is the table a reviewer should attack.</p>"
                 + table(["node", "kind", "stance", "weight"],
                         [["<span class='id'>" + esc(n["nid"]) + "</span>", esc(n["kind"]),
                           esc(n["stance"]), "<span class='id'>" + str(n["weight"]) + "</span>"]
                          for n in nodes[:14]])
                 + ("<p class='note'>" + str(len(nodes) - 14) + " further node(s) in the JSON record.</p>"
                    if len(nodes) > 14 else "") + "</section>")

    parts.append("<div class='card rehearsal'><div class='eyebrow'>Rehearsal - not a result</div>"
                 "<h2 style='margin-top:8px'>What five outcomes do to the next ruling</h2>"
                 "<p class='lede'>Four confirmations and one overturn were recorded against this"
                 " decision to show the loop moving. <b>They are synthetic: no part was measured.</b>"
                 " The engine treats them exactly as it would real ones, which is why a rehearsal"
                 " may never be filed as a result.</p><div class='stats' style='margin-top:12px'>"
                 + stat("outcomes", summary["entries"])
                 + stat("confirmed", summary["decision"]["confirmed"])
                 + stat("overturned", summary["decision"]["overturned"])
                 + stat("floor before", conf.get("floor"))
                 + stat("floor after", after.get("floor"))
                 + stat("calibrated after", "withheld" if after.get("calibrated") is None
                        else after.get("calibrated"))
                 + "</div><p class='note' style='margin-top:12px'>The same plan is re-decided"
                 " after the loop: the ruling is unchanged, the floor moved with the evidence, and"
                 " the calibrated value appeared once five outcomes existed. The overturn was"
                 " charged to the weakest decisive node automatically, because an overturn has to"
                 " be charged to something.</p></div>")

    parts.append("<div class='foot'><div class='eyebrow'>Chain</div><div class='hash'>hash "
                 + esc(d1["hash"]) + "</div><div class='hash'>prev " + esc(d1["prev_hash"])
                 + "</div><div class='hash'>source " + esc(subj.get("sha256")) + "</div>"
                 "<p class='note'>Editing any field of any record breaks the chain at that record."
                 " Reproduction: <code>py -m astracad_decide decide " + esc(plan_path)
                 + " --actor NAME</code></p></div>")

    parts.append("</div></body></html>")
    return "".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Rehearse the decision loop on a real plan.")
    ap.add_argument("plan")
    ap.add_argument("--actor", default="K M")
    ap.add_argument("--out", default="_out/decide_demo")
    args = ap.parse_args()

    utf8_console()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    store = model.DecisionStore(out / "decisions.jsonl")
    ledger = out / "outcomes.jsonl"
    for stale in (store.path, ledger):
        if stale.exists():
            stale.unlink()

    d1 = engine.evaluate(plan, actor=args.actor)
    store.append(d1)
    print(report.render(d1.to_dict()))
    print("recorded: " + d1.decision_id + "  " + d1.hash)

    record = d1.to_dict()
    script = [(outcome.CONFIRMED, "rehearsal: the audit held, no part measured"),
              (outcome.CONFIRMED, "rehearsal: the audit held, no part measured"),
              (outcome.CONFIRMED, "rehearsal: the audit held, no part measured"),
              (outcome.CONFIRMED, "rehearsal: the audit held, no part measured"),
              (outcome.OVERTURNED, "rehearsal: a reviewer released what this record held")]
    for n, (verdict, actual) in enumerate(script, 1):
        entry = outcome.record(ledger, decision=record, verdict=verdict, actual=actual,
                               actor="rehearsal harness",
                               note="rehearsal - synthetic outcome, not a measurement")
        print("outcome " + str(n) + ": " + verdict + "  charged to "
              + str(entry["attributed"]["nid"]))

    summary = outcome.summary(ledger)
    learned = outcome.reliability(ledger, model.EVIDENCE_WEIGHTS)
    priors = record["policy"]["evidence_weights"]
    kinds = [(priors[k], dict(learned[k], kind=k)) for k in priors]
    d2 = engine.evaluate(plan, actor=args.actor, summary=summary,
                         store_reliability=outcome.overrides(learned))
    print("")
    print("outcomes " + str(summary["entries"]) + "  floor before " + str(d1.confidence.floor)
          + "  after " + str(d2.confidence.floor) + "  calibrated " + str(d2.confidence.calibrated))
    (out / "record.json").write_text(model.canonical(record), encoding="utf-8")
    (out / "index.html").write_text(build_page(record, d2.to_dict(), kinds, summary, args.plan),
                                    encoding="utf-8")
    print("wrote " + str(out / "index.html"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())