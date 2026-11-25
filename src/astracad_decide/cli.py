"""Command line: decide, verify, record an outcome, inspect reliability.

Exit codes follow the other tools in this project: 0 did what was asked, 2 refused because the
input was incomplete, 4 the record is broken.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import engine, model, outcome, report

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_BROKEN = 4


def _load_json(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise ValueError("no such file: " + str(p))
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        raise ValueError(str(p.name) + ": not JSON (" + ex.msg + ")") from ex


def _plan_sha(path: str) -> str:
    return str(_load_json(path).get("source_sha256") or "")


def _default_store(plan_path: str) -> Path:
    return Path(plan_path).with_suffix(".decisions.jsonl")


def _default_outcomes(store_path: str) -> Path:
    return Path(store_path).with_suffix(".outcomes.jsonl")


def _summary_for(paths: str | None) -> tuple[dict | None, dict | None]:
    if not paths:
        return None, None
    s = outcome.summary(paths)
    table = outcome.reliability(paths, model.EVIDENCE_WEIGHTS)
    return s, outcome.overrides(table)


def cmd_decide(args: argparse.Namespace) -> int:
    plan = _load_json(args.plan)
    sources = _load_json(args.sources) if args.sources else None
    summary, overrides = _summary_for(args.outcomes)
    d = engine.evaluate(plan, actor=args.actor, sources=sources, summary=summary,
                        store_reliability=overrides)
    store = model.DecisionStore(args.store or _default_store(args.plan))
    recs = store.records()
    if recs and recs[-1].get("decision_id") == d.decision_id:
        print("unchanged: " + d.decision_id + " is already the latest record in "
              + store.path.name + " (nothing appended)")
        print(report.one_line(recs[-1]))
        return EXIT_OK
    store.append(d)
    record = d.to_dict()
    if args.json:
        print(model.canonical(record))
    else:
        print(report.render(record))
    print("recorded: " + d.decision_id + "  hash " + d.hash)
    print("store:    " + str(store.path))
    return EXIT_OK


def cmd_verify(args: argparse.Namespace) -> int:
    store = model.DecisionStore(args.store)
    plan_sha = _plan_sha(args.plan) if args.plan else None
    defects = store.verify(plan_sha256=plan_sha)
    if args.outcomes:
        defects.extend(outcome.dangling(args.outcomes, args.store))
    if defects:
        for d in defects:
            print("BROKEN " + d)
        return EXIT_BROKEN
    print("OK " + str(len(store.records())) + " record(s); chain intact"
          + ("" if plan_sha is None else ", decided on this plan"))
    return EXIT_OK


def cmd_outcome(args: argparse.Namespace) -> int:
    store = model.DecisionStore(args.store)
    recs = store.records()
    if not recs:
        raise ValueError("the store holds no decision to record an outcome against")
    if args.hash:
        decision = next((r for r in recs if r.get("hash", "").startswith(args.hash)), None)
        if decision is None:
            raise ValueError("no record with hash " + args.hash)
    else:
        decision = recs[-1]
    entry = outcome.record(args.outcomes or _default_outcomes(args.store),
                           decision=decision, verdict=args.verdict, actual=args.actual,
                           actor=args.actor, blame=args.blame, note=args.note or "")
    kind = (entry.get("attributed") or {}).get("kind")
    print("recorded " + entry["verdict"] + " for " + str(entry["decision_id"])
          + " (" + (kind or "no evidence credited") + ")")
    print(model.canonical(entry))
    return EXIT_OK


def cmd_reliability(args: argparse.Namespace) -> int:
    table = outcome.reliability(args.outcomes, model.EVIDENCE_WEIGHTS)
    s = outcome.summary(args.outcomes)
    if args.json:
        print(model.canonical({"summary": s, "kinds": table}))
        return EXIT_OK
    print("outcomes: " + str(s["entries"]) + "  confirmed " + str(s["decision"]["confirmed"])
          + "  overturned " + str(s["decision"]["overturned"]))
    print("kind                     prior  learned      n  used")
    for kind, v in table.items():
        print(kind.ljust(24) + str(v["prior"]).ljust(7) + str(v["mean"]).ljust(12)
              + str(v["n"]).ljust(3) + ("yes" if v["used"] else "no"))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="astracad_decide",
                                description="Rule on an inspection plan and keep the record.")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("decide", help="rule on a plan written by astracad_inspect")
    d.add_argument("plan")
    d.add_argument("--actor", required=True, help="name of the person making the ruling")
    d.add_argument("--store", help="decision store (default: <plan>.decisions.jsonl)")
    d.add_argument("--sources", help="JSON of independent source readings, for arbitration")
    d.add_argument("--outcomes", help="outcomes JSONL, to calibrate from what happened")
    d.add_argument("--json", action="store_true", help="print the record, not the report")
    d.set_defaults(func=cmd_decide)

    v = sub.add_parser("verify", help="check the chain and, optionally, the plan it names")
    v.add_argument("store")
    v.add_argument("--plan", help="also require a record decided on exactly this plan")
    v.add_argument("--outcomes", help="also check that every outcome refers to a stored decision")
    v.set_defaults(func=cmd_verify)

    o = sub.add_parser("outcome", help="record what actually happened")
    o.add_argument("store")
    o.add_argument("--verdict", required=True, choices=list(outcome.VERDICTS))
    o.add_argument("--actual", required=True, help="the evidence of what happened")
    o.add_argument("--actor", required=True)
    o.add_argument("--hash", help="decision hash prefix (default: the latest record)")
    o.add_argument("--blame", help="evidence node id the overturn is charged to")
    o.add_argument("--note", default="")
    o.add_argument("--outcomes", help="outcome ledger (default: <store>.outcomes.jsonl)")
    o.set_defaults(func=cmd_outcome)

    r = sub.add_parser("reliability", help="learned weight per evidence kind")
    r.add_argument("outcomes")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_reliability)
    return p


def utf8_console() -> None:
    """A drawing carries symbols the Windows console codepage does not, and a report that dies
    on a diameter sign is not a record. Replace unencodable characters instead of raising."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as ex:
        print("refused: " + str(ex), file=sys.stderr)
        return EXIT_REFUSED