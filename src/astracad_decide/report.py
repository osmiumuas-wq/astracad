"""Render a decision as the document a quality engineer would actually read.

Order matters: the ruling, then why, then what is missing, then which evidence can be attacked,
and only last the hashes. A reader who disagrees should know within one screen which single
piece of evidence to attack.
"""
from __future__ import annotations

from .model import RELEASE


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        cells = [str(x).replace("|", "\\|") for x in r]
        out.append("| " + " | ".join(cells) + " |")
    return out


def render(decision: dict, *, max_rows: int = 60) -> str:
    d = decision
    did = d.get("decision_id")
    disposition = d.get("disposition")
    subj = d.get("subject", {}) or {}
    conf = d.get("confidence", {}) or {}
    lines = []

    lines.append("# Decision " + str(did) + "   " + str(disposition))
    lines.append("")
    lines.append("**Question.** " + str(d.get("question")))
    lines.append("")
    rev = str(subj.get("revision") or "(none)")
    if not subj.get("revision_stated", True):
        rev += " (assumed - the drawing does not state one)"
    lines.append("**Subject.** " + str(subj.get("document"))
                 + " · sha256 `" + str(subj.get("sha256")) + "`"
                 + " · revision " + rev
                 + " · unit " + str(subj.get("unit") or "?")
                 + " · " + str(subj.get("characteristics")) + " characteristic(s)")
    lines.append("")
    lines.append("**Ruling.** " + str(d.get("rationale")))
    lines.append("")
    lines.append("**Stated by.** " + str(d.get("actor")) + " at " + str(d.get("created_utc")))
    lines.append("")

    items = d.get("items") or []
    counts = {}
    for i in items:
        key = i.get("disposition")
        counts[key] = counts.get(key, 0) + 1
    summary = " · ".join(str(k) + " " + str(v) for k, v in sorted(counts.items())) or "none"
    lines.append("## Callouts (" + summary + ")")
    lines.append("")
    rows = []
    for i in items[:max_rows]:
        rows.append([i.get("cid"), "`" + str(i.get("callout")) + "`",
                     i.get("disposition"), i.get("because")])
    if rows:
        lines.extend(_table(["id", "callout", "ruling", "because"], rows))
    if len(items) > max_rows:
        lines.append("")
        lines.append("*… " + str(len(items) - max_rows) + " further callout(s) in the record.*")
    lines.append("")

    contras = d.get("contradictions") or []
    lines.append("## Contradictions (" + str(len(contras)) + ")")
    lines.append("")
    if contras:
        lines.extend(_table(["severity", "subject", "what cannot both be true"],
                            [[c.get("severity"), c.get("subject"), c.get("message")]
                             for c in contras]))
    else:
        lines.append("None: no two pieces of evidence disagree.")
    lines.append("")

    unknown = d.get("unknowns") or []
    lines.append("## What is missing (" + str(len(unknown)) + ")")
    lines.append("")
    if unknown:
        lines.extend(_table(["code", "subject", "blocks", "message"],
                            [[u.get("code"), u.get("subject"), u.get("blocks"),
                              u.get("message")] for u in unknown]))
    else:
        lines.append("Nothing the ruling needs is absent from the source.")
    lines.append("")

    floor = conf.get("floor")
    calibrated = conf.get("calibrated")
    lines.append("## Confidence")
    lines.append("")
    lines.append("- method: **" + str(conf.get("method")) + "**")
    lines.append("- evidence floor: " + ("—" if floor is None else str(floor)))
    lines.append("- calibrated: " + ("withheld" if calibrated is None else str(calibrated))
                 + " (n = " + str(conf.get("n", 0)) + " recorded outcome(s))")
    lines.append("- " + str(conf.get("note")))
    lines.append("")

    lines.append("## Rulings not taken")
    lines.append("")
    for a in d.get("alternatives") or []:
        lines.append("- **" + str(a.get("disposition")) + "** — " + str(a.get("why_not")))
    lines.append("")

    lines.append("## Smallest change that would move this ruling")
    lines.append("")
    for n, a in enumerate(d.get("next_actions") or [], 1):
        lines.append(str(n) + ". " + str(a))
    lines.append("")

    nodes = d.get("evidence") or []
    want = "supports" if disposition == RELEASE else "opposes"
    decisive = [n for n in nodes if n.get("stance") == want]
    lines.append("## Evidence (" + str(len(nodes)) + " node(s), "
                 + str(len(decisive)) + " decisive)")
    lines.append("")
    rows = []
    for n in nodes[:max_rows]:
        rows.append([n.get("nid"), n.get("kind"), n.get("stance"), n.get("weight"),
                     n.get("claim")])
    if rows:
        lines.extend(_table(["node", "kind", "stance", "weight", "claim"], rows))
    if len(nodes) > max_rows:
        lines.append("")
        lines.append("*… " + str(len(nodes) - max_rows) + " further node(s) in the record.*")
    lines.append("")

    lines.append("## Chain")
    lines.append("")
    lines.append("- hash: `" + str(d.get("hash")) + "`")
    lines.append("- prev: `" + str(d.get("prev_hash")) + "`")
    lines.append("- policy: " + str(d.get("policy")))
    lines.append("")
    return "\n".join(lines)


def one_line(decision: dict) -> str:
    conf = decision.get("confidence", {}) or {}
    return (" ".join([str(decision.get("decision_id")), str(decision.get("disposition")),
                      str(len(decision.get("contradictions") or [])) + " contradiction(s)",
                      str(len(decision.get("unknowns") or [])) + " unknown(s)",
                      "floor=" + str(conf.get("floor"))]))