"""No-drawing checks: the rules that must hold regardless of any input file.

These are the honesty invariants of the engine - that an ambiguous notation is not guessed, that
an unresolved fit class produces no numbers, that balloons never collide. They run in well under
a second and need no CAD file, so a regression in a rule is caught before anything is exported.
"""
from __future__ import annotations

from .balloons import layout_balloons, overlaps
from .callouts import parse_callout
from .limits import deviation_band, evaluate, resolve_limits
from .model import (CharKind, Characteristic, Deviations, FitClass, LimitPair, NoTolerance,
                    ProvenanceState, SourceLocation, Symmetric)
from .units import INSUNITS_TO_UNIT, UnitError, to_mm


def _char(cid: str, x: float, y: float) -> Characteristic:
    c = Characteristic(cid=cid, kind=CharKind.LINEAR, raw_callout="10", unit="mm", nominal=10.0)
    c.source = SourceLocation(document="selftest", document_sha256="0" * 64, revision="A",
                              page=1, entity_handle=cid, entity_type="DIMENSION", layer="0",
                              anchor_x=x, anchor_y=y)
    return c


def run_selftest(print_output: bool = True) -> int:
    total = 0
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal total, failures
        total += 1
        if print_output:
            print(f"{'PASS' if ok else 'FAIL':<5} {name}" + (f"  -- {detail}" if detail else ""))
        if not ok:
            failures += 1

    # --- the parser refuses to invent ------------------------------------------------------
    p = parse_callout("86.98 87.05")
    check("two bare numbers are not read as a tolerance range", not p.resolved,
          p.unresolved_reason or "")
    p = parse_callout("20 H7")
    check("ISO fit class parses as a class, not as a guess",
          p.resolved and isinstance(p.tolerance, FitClass), p.unresolved_reason or "")
    lim = resolve_limits(20.0, FitClass(hole="H7", shaft=None), "mm")
    check("20 H7 takes its limits from the published ISO 286-2 table",
          lim == (20.0, 20.021), str(lim))
    check("a class over a size step the published tables print as two header columns is read "
          "from the step",
          resolve_limits(45.0, FitClass(hole="H7", shaft=None), "mm") == (45.0, 45.025),
          str(resolve_limits(45.0, FitClass(hole="H7", shaft=None), "mm")))
    check("a class the loaded table does not confirm stays unresolved",
          resolve_limits(450.0, FitClass(hole="H7", shaft=None), "mm") is None)
    check("a fit pair names two features and resolves neither",
          resolve_limits(25.0, FitClass(hole="H7", shaft="g6"), "mm") is None)
    check("an inch callout is not resolved from the metric table",
          resolve_limits(0.75, FitClass(hole="H7", shaft=None), "in") is None)
    p = parse_callout("M8-1.25")
    check("thread designation is not read as a size dimension", not p.resolved,
          p.unresolved_reason or "")
    p = parse_callout("GAP")
    check("text with no number is unresolved", not p.resolved)

    # --- worked examples from the project report ------------------------------------------
    p = parse_callout("53 +0.01/-0.02")
    lim = resolve_limits(p.nominal, p.tolerance, "mm")
    check("53 +0.01/-0.02 gives 53.01/52.98", lim == (52.98, 53.01), str(lim))
    p = parse_callout("\u23005 \u00b10.15")
    lim = resolve_limits(p.nominal, p.tolerance, "mm")
    check("\u23005 \u00b10.15 gives 5.15/4.85", lim == (4.85, 5.15), str(lim))
    check("diameter sign is recognised", p.kind is CharKind.DIAMETER, p.kind.value)
    check("deviation sign convention enforced",
          deviation_band(Deviations(upper=0.01, lower=-0.02), "mm") == 0.03)

    # --- notation variants that must normalise to the same reading -------------------------
    spellings = [parse_callout(raw) for raw in
                 ("\u230010", "\u00d810", "%%c10", "10 DIA", "dia 10")]
    check("five spellings of a diameter all read as 10",
          all(s.nominal == 10.0 for s in spellings),
          str({s.nominal for s in spellings}))
    check("and all five are recognised as diameters, not widths",
          all(s.kind is CharKind.DIAMETER for s in spellings),
          str({s.kind.value for s in spellings}))
    check("%%c escape is normalised", parse_callout("%%c10").kind is CharKind.DIAMETER)
    check("spherical radius reads as a radius", parse_callout("SR12").kind is CharKind.RADIUS)
    check("count prefix does not become the nominal",
          parse_callout("4X \u230010 THRU").nominal == 10.0
          and parse_callout("4X \u230010 THRU").count == 4)
    a = parse_callout("45\u00b0")
    check("degrees parse as angular", a.kind is CharKind.ANGULAR and a.nominal == 45.0)
    dms = parse_callout("45\u00b030'")
    check("degree-minute parses", dms.nominal == 45.5, str(dms.nominal))
    lp = parse_callout("10.05/10.00")
    check("limit pair keeps both numbers and no nominal",
          isinstance(lp.tolerance, LimitPair) and lp.nominal is None)
    check("no tolerance stated is not the same as zero tolerance",
          resolve_limits(10.0, NoTolerance(), "mm") is None)

    # --- units -----------------------------------------------------------------------------
    check("$INSUNITS 0 declares no unit", INSUNITS_TO_UNIT[0] is None)
    check("inches convert to millimetres", abs(to_mm(1.0, "in") - 25.4) < 1e-9)
    try:
        to_mm(1.0, "deg")
        check("a degree cannot be converted as a length", False)
    except UnitError:
        check("a degree cannot be converted as a length", True)

    # --- verdicts --------------------------------------------------------------------------
    check("measured at the limit passes (inclusive)",
          evaluate(53.01, (52.98, 53.01), "mm") == "pass")
    check("measured outside fails", evaluate(53.02, (52.98, 53.01), "mm") == "fail")
    check("no limits means no verdict", evaluate(53.0, None, "mm") == "review_required")

    # --- balloons --------------------------------------------------------------------------
    content = (0.0, 0.0, 100.0, 50.0)
    chars = [_char(f"CH-{i:03d}", 10.0 * i, 25.0) for i in range(1, 13)]
    layout = layout_balloons(chars, content)
    numbers = [b.number for b in layout.balloons]
    check("balloon numbers are unique", len(numbers) == len(set(numbers)), f"{len(numbers)} placed")
    check("no balloon overlaps another", not overlaps(layout.balloons))
    check("every balloon sits outside the part outline",
          all(b.centre[1] < content[1] or b.centre[1] > content[3]
              or b.centre[0] < content[0] or b.centre[0] > content[2]
              for b in layout.balloons))
    again = layout_balloons([_char(f"CH-{i:03d}", 10.0 * i, 25.0) for i in range(1, 13)], content)
    check("layout is deterministic",
          [(b.cid, b.centre) for b in layout.balloons] == [(b.cid, b.centre) for b in again.balloons])

    # --- gate ------------------------------------------------------------------------------
    from .ingest.dxf import DrawingBundle, EntityCensusRow
    from .plan import build_plan

    blocked = _char("CH-001", 50.0, 0.0)
    blocked.state = ProvenanceState.UNRESOLVED
    bundle = DrawingBundle(path="selftest.dxf", sha256="0" * 64, revision="A", unit="mm",
                           extents=(-10.0, -10.0, 110.0, 60.0),
                           content_extents=content,
                           characteristics=[blocked],
                           census=[EntityCensusRow("DIMENSION", 1, 1, "")])
    plan = build_plan(bundle)
    check("an unresolved characteristic blocks release", not plan.approvable, plan.gate_statement())
    check("the gate names the reason", "blocked" in plan.gate_statement().lower())

    if print_output:
        print(f"{'-' * 60}\n{total - failures}/{total} selftest checks passed")
    return 0 if failures == 0 else 1
