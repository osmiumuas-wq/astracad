"""`inspect` - the command surface for ASTraCAD Inspect.

Exit codes are engineering statements, not success theatre:

    0  the run delivered and the plan is releasable
    3  the run delivered, but the plan is NOT releasable (findings need an engineer)
    2  bad usage
    1  the run could not be completed

A drawing with unresolved characteristics is a normal, useful outcome, so 3 exists to keep that
different from a crash without ever reporting "all good".
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from . import iso286
from .model import CheckOutcome
from .plan import apply_overrides, build_plan
from .pipeline import inspect_drawing, read_drawing
from .provenance import tool_versions

UNITS = ("mm", "cm", "m", "in", "ft", "um")


def _num(v, width: int, decimals: int) -> str:
    """Right-aligned in a fixed column. A dash means the drawing stated nothing - it does not
    mean zero, and it does not mean exact."""
    return f"{'-':>{width}}" if v is None else f"{v:>{width}.{decimals}f}"


def _print_plan(plan, *, rows: bool = True) -> None:
    tb = plan.title_block
    print(f"SOURCE     {plan.source_document} sha256:{plan.source_sha256[:16]}... "
          f"rev {plan.revision} ({plan.revision_source}) unit={plan.unit}")
    if tb:
        print(f"TITLE BLOCK {tb}")
    print(f"GATE       {plan.gate_statement()}")
    cov = plan.coverage
    print(f"COVERAGE   dimensions={cov.dimensions_in_source} extracted={cov.characteristics_extracted}"
          f" ({cov.extraction_rate_text}) resolved={cov.resolved} unresolved={cov.unresolved} "
          f"blocked={cov.blocking} geo_ok={cov.geometry_cross_check_passed}"
          f"/{cov.geometry_cross_checked} balloons={cov.balloon_assigned} "
          f"unplaced={cov.balloon_unplaced} escalated={cov.annotations_escalated}")
    for c in plan.checks:
        mark = "ok  " if c.outcome is CheckOutcome.PASSED else (
            "n/a " if c.outcome is CheckOutcome.NOT_VERIFIABLE else "BAD ")
        print(f"  {mark}{c.name:<44} expected={c.expected} actual={c.actual}")
    if not rows:
        return
    print("ROWS       balloon  type      callout                     nominal  tol"
          "            min       max       status")
    for r in plan.rows:
        print(f"  {str(r.balloon_no):>6}  {r.kind:<9} {r.callout[:27]:<27} "
              f"{_num(r.nominal, 8, 3)}  {r.tolerance_text[:14]:<14} "
              f"{_num(r.minimum, 9, 4)} {_num(r.maximum, 9, 4)}  "
              f"{r.provenance}{'/blocked' if r.blocking else ''}")
    for u in plan.uninterpreted:
        print(f"  ESCALATED text {u.text!r} on layer {u.layer}: {u.reason}")


def cmd_extract(a) -> int:
    bundle, _ = read_drawing(a.drawing, a.units, a.revision)
    from .pipeline import apply_general_tolerance
    apply_general_tolerance(bundle, getattr(a, "general_tolerance", None))
    if a.overrides:
        apply_overrides(bundle, a.overrides)
    plan = build_plan(bundle, balloon_radius=a.balloon_radius)
    _print_plan(plan)
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False,
                                           default=str) + "\n", encoding="utf-8")
        print(f"JSON       {a.json}")
    return 0 if plan.approvable else 3


def cmd_run(a) -> int:
    res = inspect_drawing(a.drawing, a.out, declared_unit=a.units, revision=a.revision,
                          general_tolerance=a.general_tolerance,
                          overrides=a.overrides, balloon_radius=a.balloon_radius)
    _print_plan(res.plan)
    for k, v in sorted(res.artifacts.items()):
        print(f"ARTIFACT   {k:<9} {v}")
    bad = [c for c in res.verification if c.outcome is not CheckOutcome.PASSED]
    print(f"EXPORT     {len(res.verification) - len(bad)}/{len(res.verification)} reopen checks "
          f"passed" + ("" if not bad else " :: " + ", ".join(c.name for c in bad)))
    if not res.delivered:
        print("RESULT     the deliverables did not read back correctly - do not use them")
        return 1
    print(f"RESULT     {'releasable' if res.plan.approvable else 'delivered, NOT releasable'}")
    return 0 if res.plan.approvable else 3


def cmd_fai(a) -> int:
    from .export.fai import verify_fai, write_fai
    out = write_fai(a.plan_json, a.out)
    checks = verify_fai(a.plan_json, out)
    for name, ok, detail in checks:
        print(f"  {'ok ' if ok else 'BAD'} {name:<24} {detail}")
    print(f"ARTIFACT   fai   {out}")
    print("NOTE       results, serial numbers, organisation data and Form 2 are left blank to be "
          "completed by the organisation performing the FAI")
    return 0 if all(ok for _, ok, _ in checks) else 1


def cmd_doctor(a) -> int:
    print(f"ENGINE     astracad-inspect {__version__} (python {sys.version.split()[0]})")
    for name, version in sorted(tool_versions().items()):
        print(f"  {name:<12} {version}")
    routes = {
        "dxf-vector": "available",
        "pdf-vector": "available - text only: callouts need review, no geometry cross-check",
        "raster-ocr": "available - two OCR engines must agree; every row needs review",
        "gdt-frames": "DXF TOLERANCE entities; Unicode frames in PDF/OCR text",
        "iso-286-fits": iso286.support_statement(),
        "solidworks-live": "delegated to the `mac` runtime in mecagent-alternative",
    }
    print("ROUTES")
    for k, v in routes.items():
        print(f"  {k:<18} {v}")
    import importlib.util
    reader = ("pypdfium2 (Apache-2.0/BSD-3)" if importlib.util.find_spec("pypdfium2") else
              "PyMuPDF (AGPL-3.0)" if importlib.util.find_spec("pymupdf") else
              "NONE - PDF exports will be reported not_verifiable")
    print(f"PDF REOPEN {reader}")
    print("LICENCES   pypdfium2 is preferred for PDF read-back (permissive). If only PyMuPDF is\n"
          "           present it is used, and PyMuPDF is AGPL-3.0: distributing a build that\n"
          "           depends on it carries source-availability obligations.")
    return 0


def cmd_compare(a) -> int:
    from .revision import compare_drawings, write_changeset
    cs = compare_drawings(a.old, a.new, unit=a.units, old_revision=a.old_revision,
                          new_revision=a.new_revision)
    out = write_changeset(cs, a.out, f"{Path(a.old).stem}_vs_{Path(a.new).stem}")
    print(f"COMPARE    {a.old} (rev {cs.old_revision}) -> {a.new} (rev {cs.new_revision})")
    for k, v in sorted(cs.summary().items()):
        print(f"  {k:<18} {v}")
    for c in cs.changes:
        if c.status != "UNCHANGED":
            flag = "REVIEW" if c.requires_review else "      "
            print(f"  {flag} {c.status:<18} {c.old_cid or '-':<7} -> {c.new_cid or '-':<7} "
                  f"{c.detail}")
    for n in cs.notes_added:
        print(f"  REVIEW NOTE_ADDED          {n}")
    for n in cs.notes_removed:
        print(f"  REVIEW NOTE_REMOVED        {n}")
    for k, path in out.items():
        print(f"ARTIFACT   {k:<5} {path}")
    print("RESULT     " + ("changes need human review" if cs.needs_review
                           else "no consequential change"))
    return 3 if cs.needs_review else 0


def cmd_selftest(a) -> int:
    from .selftest import run_selftest
    return run_selftest(print_output=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="inspect",
                                description="ASTraCAD Inspect - inspection layout from 2D "
                                            "engineering drawings")
    p.add_argument("--version", action="version", version=f"astracad-inspect {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, out=False):
        sp.add_argument("drawing", help="path to the drawing (.dxf; .pdf or a scan .png/.jpg/.tif with --units)")
        sp.add_argument("--units", choices=UNITS, default=None,
                        help="unit basis; required when the DXF does not declare $INSUNITS")
        sp.add_argument("--revision", default=None,
                       help="override the revision letter; by default it is taken from the "
                            "drawing's title block, and a value this build falls back to is "
                            "recorded as a default rather than as read content")
        sp.add_argument("--general-tolerance", dest="general_tolerance", default=None,
                        help="iso2768-<f|m|c|v> or places:2=0.01,3=0.005 for rows that state no "
                             "tolerance (default: a statement found on the drawing, else none)")
        sp.add_argument("--overrides", default=None,
                        help="JSON correction file from an engineer")
        sp.add_argument("--balloon-radius", type=float, default=4.0, dest="balloon_radius",
                        help="balloon radius in drawing units (default 4)")
        if out:
            sp.add_argument("--out", default="out", help="output directory")

    sp = sub.add_parser("extract", help="read a drawing and print the plan, writing nothing")
    common(sp)
    sp.add_argument("--json", default=None, help="also write the plan to this path")
    sp.set_defaults(func=cmd_extract)

    sp = sub.add_parser("run", help="extract, balloon, export PDF + XLSX, and re-open to verify")
    common(sp, out=True)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("doctor", help="report library versions, routes and licence obligations")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("compare", help="compare two revisions of a drawing; writes a change set")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.add_argument("--units", choices=UNITS, default=None)
    sp.add_argument("--old-revision", default=None, dest="old_revision")
    sp.add_argument("--new-revision", default=None, dest="new_revision")
    sp.add_argument("--out", default="out")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("fai", help="AS9102-structured FAI workbook (Forms 1-3) from a plan JSON")
    sp.add_argument("plan_json")
    sp.add_argument("--out", required=True, help="output .xlsx")
    sp.set_defaults(func=cmd_fai)
    sp = sub.add_parser("selftest", help="run the no-drawing checks")
    sp.set_defaults(func=cmd_selftest)
    return p


def main(argv: list[str] | None = None) -> int:
    # A Windows console often runs a legacy code page that cannot encode ⌀ or ±. Losing the glyph
    # is cosmetic; aborting a passing run because its report could not be printed is not.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, KeyError) as ex:
        print(f"ERROR      {type(ex).__name__}: {ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
