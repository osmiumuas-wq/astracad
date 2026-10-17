"""py -m astracad_mfg cost --kernel-report R.report.json --step P.step --rates rates.json --job job.json --out DIR

Exit codes: 0 estimate written and re-read; 2 inputs missing or invalid (nothing written); 1 the
written files did not read back identically.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .costing import CostingError, estimate, geometry_from_evidence
from .report import verify, write


def _load(path: str, what: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError as ex:
        raise ValueError(f"{what} {path} is not valid JSON ({ex})") from ex


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="astracad-mfg")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cost", help="explainable machining cost from CAD evidence + sourced rates")
    c.add_argument("--kernel-report", required=True, help="mac run report (*.report.json)")
    c.add_argument("--step", required=True, help="the part's exported STEP (independent size)")
    c.add_argument("--rates", required=True, help="rate book JSON (every value sourced and dated)")
    c.add_argument("--job", required=True, help="job JSON: material, machine, quantity, times")
    c.add_argument("--out", required=True)
    c.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    try:
        geo = geometry_from_evidence(a.kernel_report, a.step)
        est = estimate(geo, _load(a.rates, "rate book"), _load(a.job, "job file"))
    except (CostingError, OSError, ValueError) as ex:
        print(f"NOT ESTIMATED  {ex}")
        return 2
    name = a.name or Path(a.step).stem
    files = write(est, a.out, name)
    for ln in est.lines:
        print(f"  {ln.name:<16} {ln.value:>14.4f} {ln.unit:<6} = {ln.formula}")
    for w in est.warnings:
        print(f"WARNING    {w}")
    checks = verify(est, files)
    for nm, ok, detail in checks:
        print(f"  {'ok ' if ok else 'BAD'} {nm:<26} {detail}")
    print(f"COST       {est.cost_per_part:.2f} {est.currency}/part, batch of {est.quantity}: "
          f"{est.batch_cost:.2f} {est.currency}  ({est.scope_note})")
    for k, p in files.items():
        print(f"ARTIFACT   {k:<5} {p}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
