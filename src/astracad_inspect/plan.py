"""Inspection plan: balloon numbering, the inspection table, and the release gate.

The plan is built by re-deriving, not by trusting. Limits computed during extraction are
recomputed here and cross-checked against the tolerance band by a second, arithmetically
different route; every row must link back to an entity in the drawing it came from. A plan with
unresolved or disagreeing characteristics is reported as not releasable — listing the count of
outstanding items is the point of the gate, not an inconvenience to route around.

Manual corrections arrive as an override file and are recorded in each characteristic's history
with the actor that made them. An override changes provenance to user-supplied or
human-approved; it never silently rewrites the original callout text.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import iso286
from .balloons import Layout, inside_content, layout_balloons, overlaps
from .callouts import normalise, parse_callout
from .limits import deviation_band, resolve_limits
from .model import (
    CharKind,
    Check,
    CheckOutcome,
    Characteristic,
    Deviations,
    FitClass,
    GeneralTolerance,
    GeometricTolerance,
    Issue,
    LimitPair,
    NoTolerance,
    ProvenanceState,
    ReviewStatus,
    Symmetric,
    UninterpretedAnnotation,
)
from .units import UnitError

# A suggestion table, not an approved process route. The engineer owns the method choice; the
# plan only refuses to leave the column blank when the kind is known.
MEASUREMENT_METHODS = {
    CharKind.LINEAR: ("direct linear measurement",
                      "caliper, micrometer, or CMM distance between two faces"),
    CharKind.DIAMETER: ("diameter / bore measurement",
                        "bore gauge, three-wire, or CMM circle fit"),
    CharKind.RADIUS: ("profile radius", "radius gauge or CMM profile of the arc"),
    CharKind.ANGULAR: ("included angle", "bevel protractor or CMM two-plane angle"),
    CharKind.GEOMETRIC: ("geometric tolerance evaluation",
                         "CMM evaluation per ISO 1101 / ASME Y14.5 in the stated datum reference frame"),
    CharKind.UNKNOWN: ("<method undetermined>",
                       "the characteristic kind is unresolved, so no method is recommended"),
}


# Checks whose not-verifiable outcome records a reviewer-owned exception rather than an engine
# gap. Naming them explicitly keeps "approved" from becoming a way to make a finding disappear.
ACCEPTED_EXCEPTION_CHECKS = frozenset({
    "geometry_disagreements_accepted_by_reviewer",
    "reviewer_supplied_rows_without_source_link",
})


def describe_tolerance(tol, limits: tuple[float, float] | None = None) -> str:
    """Render the tolerance exactly as it was interpreted, including the unresolved cases."""
    if isinstance(tol, NoTolerance):
        return "none stated"
    if isinstance(tol, Symmetric):
        return f"±{tol.plus_minus:g}"
    if isinstance(tol, Deviations):
        return f"+{tol.upper:g}/-{abs(tol.lower):g}"
    if isinstance(tol, LimitPair):
        return f"{tol.maximum:g}/{tol.minimum:g}"
    if isinstance(tol, GeneralTolerance):
        return f"±{tol.plus_minus:g} (general: {tol.basis})"
    if isinstance(tol, GeometricTolerance):
        from .gdt import describe
        return describe(tol)
    if isinstance(tol, FitClass):
        parts = [v for v in (tol.hole, tol.shaft) if v]
        if limits is None or not parts:
            return f"{'/'.join(parts)} (fit class, limits not resolved)"
        return f"{parts[0]} (ISO 286-2: {limits[0]:g} to {limits[1]:g} mm)"
    return repr(tol)


@dataclass
class InspectionRow:
    """One line of the inspection table."""
    balloon_no: int | None
    cid: str
    kind: str
    raw_callout: str
    callout: str
    nominal: float | None
    unit: str
    tolerance_text: str
    minimum: float | None
    maximum: float | None
    count: int
    method: str
    method_basis: str
    status: str
    provenance: str
    blocking: bool
    issues: list[str]
    source_document: str
    source_sha256: str
    entity_handle: str
    entity_type: str
    layer: str
    page: int


@dataclass
class Coverage:
    """Every ratio here is reported with its denominator; none of them implies the rest of the
    drawing is understood."""
    dimensions_in_source: int
    characteristics_extracted: int
    resolved: int
    unresolved: int
    blocking: int
    geometry_cross_checked: int
    geometry_cross_check_passed: int
    balloon_assigned: int
    balloon_unplaced: int
    annotations_escalated: int

    @property
    def extraction_rate(self) -> float | None:
        """None when the source has no dimension entities to count against (PDF text, scans):
        a ratio over zero would print as 0% or 100% and mean neither."""
        if not self.dimensions_in_source:
            return None
        return self.characteristics_extracted / self.dimensions_in_source

    @property
    def extraction_rate_text(self) -> str:
        r = self.extraction_rate
        return "n/a - no dimension entities to count against" if r is None else f"{r:.0%}"


@dataclass
class InspectionPlan:
    source_document: str
    source_sha256: str
    revision: str
    unit: str
    page_count: int
    extents: tuple[float, float, float, float]
    content_extents: tuple[float, float, float, float]
    characteristics: list[Characteristic]
    rows: list[InspectionRow]
    layout: Layout
    checks: list[Check]
    issues: list[Issue]
    uninterpreted: list[UninterpretedAnnotation]
    coverage: Coverage
    title_block: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    revision_source: str = "default - not stated by the drawing"
    warnings: list[str] = field(default_factory=list)
    feature_support: dict[str, str] = field(default_factory=dict)
    census: list[dict] = field(default_factory=list)

    @property
    def blocking_characteristics(self) -> list[Characteristic]:
        return [c for c in self.characteristics if c.is_blocking]

    @property
    def checks_passed(self) -> int:
        return sum(1 for c in self.checks if c.outcome is CheckOutcome.PASSED)

    @property
    def accepted_exceptions(self) -> list[Check]:
        """Checks that report an exception a named reviewer took responsibility for. They are
        never counted as passed: the drawing still disagrees, a person overrode it."""
        return [c for c in self.checks
                if c.name in ACCEPTED_EXCEPTION_CHECKS
                and c.outcome is CheckOutcome.NOT_VERIFIABLE]

    @property
    def open_gaps(self) -> list[Check]:
        return [c for c in self.checks
                if c.outcome is not CheckOutcome.PASSED
                and c not in self.accepted_exceptions]

    @property
    def approvable(self) -> bool:
        """Gate: nothing blocked, nothing unplaced, and every check either passed or explicitly
        accepted as an exception by a reviewer."""
        return (not self.blocking_characteristics
                and not self.layout.unplaced
                and not self.open_gaps)

    def gate_statement(self) -> str:
        if self.approvable:
            base = ("releasable: every characteristic resolved, cross-checked and ballooned"
                    if not self.accepted_exceptions else
                    f"releasable with {len(self.accepted_exceptions)} accepted exception(s) — "
                    "the exceptions are listed and were not verified away")
            return base
        reasons = []
        if self.blocking_characteristics:
            reasons.append(f"{len(self.blocking_characteristics)} characteristic(s) blocked")
        if self.layout.unplaced:
            reasons.append(f"{len(self.layout.unplaced)} balloon(s) unplaced")
        if self.open_gaps:
            reasons.append("check(s) not passed: "
                           + ", ".join(c.name for c in self.open_gaps))
        return "NOT releasable — " + "; ".join(reasons)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["approvable"] = self.approvable
        d["gate"] = self.gate_statement()
        d["accepted_exceptions"] = [asdict(c) for c in self.accepted_exceptions]
        d["open_gaps"] = [asdict(c) for c in self.open_gaps]
        d["layout"] = {
            "page": list(self.layout.page),
            "slot_pitch": self.layout.slot_pitch,
            "ring_offset": self.layout.ring_offset,
            "unplaced": self.layout.unplaced,
            "balloons": [
                {"number": b.number, "cid": b.cid,
                 "centre": list(b.centre), "radius": b.radius,
                 "leader": [list(p) for p in b.leader]}
                for b in self.layout.balloons],
        }
        d["checks"] = [asdict(c) for c in self.checks]
        d["coverage"] = asdict(self.coverage)
        d["coverage"]["extraction_rate"] = self.coverage.extraction_rate
        return d


def _row(c: Characteristic, methods: dict[str, str]) -> InspectionRow:
    method, basis = MEASUREMENT_METHODS.get(c.kind, MEASUREMENT_METHODS[CharKind.UNKNOWN])
    if not methods.get("accept_suggested_methods", True):
        method = "<not accepted by override>"
    src = c.source
    link = dict(
        source_document="<not from the drawing>", entity_handle="", entity_type="",
        layer="", page=0, source_sha256="")
    if src is not None:
        link = dict(source_document=src.document, entity_handle=src.entity_handle,
                    entity_type=src.entity_type, layer=src.layer, page=src.page,
                    source_sha256=src.document_sha256)
    return InspectionRow(
        balloon_no=c.balloon_no,
        cid=c.cid,
        kind=c.kind.value,
        raw_callout=c.raw_callout,
        # A frame's source text is font codes ({\Fgdt;j}%%v...); the readable frame is shown and
        # the raw text stays untouched in raw_callout.
        callout=(describe_tolerance(c.tolerance) if isinstance(c.tolerance, GeometricTolerance)
                 else normalise(c.raw_callout)),
        nominal=c.nominal,
        unit=c.unit,
        tolerance_text=describe_tolerance(c.tolerance, c.limits),
        minimum=None if c.limits is None else c.limits[0],
        maximum=None if c.limits is None else c.limits[1],
        count=c.count,
        method=method,
        method_basis=basis,
        status=c.review.value,
        provenance=c.state.value,
        blocking=c.is_blocking,
        issues=[f"{i.severity}:{i.code}" for i in c.issues],
        **link,
    )


def _check_limits(c: Characteristic, unit: str, failures: list[str]) -> bool:
    """Second route: recompute and then verify the pair against the band and the nominal, which
    the first route never does together."""
    try:
        recomputed = resolve_limits(c.nominal, c.tolerance, unit)
    except (UnitError, AssertionError, ValueError) as ex:
        failures.append(f"{c.cid}: limits cannot be recomputed ({ex})")
        return False
    if (c.limits is not None and recomputed is not None
            and c.limits != recomputed):
        failures.append(f"{c.cid}: stored limits {c.limits} differ from recomputed {recomputed}")
        return False
    if c.limits is None:
        return True
    lo, hi = c.limits
    try:
        band = deviation_band(c.tolerance, unit)
    except (UnitError, ValueError) as ex:
        failures.append(f"{c.cid}: band cannot be evaluated ({ex})")
        return False
    if isinstance(c.tolerance, FitClass) and c.limits is not None:
        # A letter code states a band, not a pair of limits. This second route rebuilds the pair
        # from the published deviations in micrometres, along an arithmetic path that does not go
        # through resolve_limits, and refuses a row claiming limits with no size to hang them on.
        code = c.tolerance.hole or c.tolerance.shaft
        got = iso286.resolve_fit(c.nominal, code) if c.nominal is not None else None
        if got is None or not got.resolved:
            failures.append(f"{c.cid}: fit class {code} carries limits that the ISO 286-2 table "
                            f"does not state for nominal {c.nominal}")
            return False
        rebuilt = (round(c.nominal + got.lower_um / 1000.0, 9),
                   round(c.nominal + got.upper_um / 1000.0, 9))
        if rebuilt != c.limits:
            failures.append(f"{c.cid}: fit class limits {c.limits} do not rebuild from the "
                            f"published deviations ({rebuilt})")
            return False
    if isinstance(c.tolerance, (Symmetric, Deviations, GeneralTolerance)):
        if abs((hi - lo) - band) > 1e-9:
            failures.append(f"{c.cid}: limit span {hi - lo:g} does not equal the tolerance "
                            f"band {band:g}")
            return False
        if c.nominal is None or not (lo - 1e-9 <= c.nominal <= hi + 1e-9):
            failures.append(f"{c.cid}: nominal {c.nominal} lies outside its own limits "
                            f"({lo:g}, {hi:g})")
            return False
    if isinstance(c.tolerance, GeometricTolerance):
        if (lo, hi) != (0.0, c.tolerance.zone) or c.nominal is not None:
            failures.append(f"{c.cid}: geometric tolerance limits {c.limits} are not 0 .. zone "
                            f"{c.tolerance.zone:g}")
            return False
    if isinstance(c.tolerance, LimitPair):
        if (hi, lo) != (c.tolerance.maximum, c.tolerance.minimum):
            failures.append(f"{c.cid}: limit pair was rewritten in transit")
            return False
    return True


def build_plan(bundle, balloon_radius: float = 4.0, gap: float = 2.0,
               margin: float = 14.0, methods: dict[str, str] | None = None) -> InspectionPlan:
    """Turn an ingested drawing bundle into a validated inspection plan."""
    methods = methods or {}
    chars = sorted(bundle.characteristics, key=lambda c: c.cid)
    checks: list[Check] = []
    plan_issues: list[Issue] = []

    layout = layout_balloons(chars, bundle.content_extents, balloon_radius, gap, margin)
    rows = [_row(c, methods) for c in chars]
    manual_rows = [r for r in rows if not r.entity_handle]

    def add(name: str, ok: bool, expected: str, actual: str, why: str | None = None) -> None:
        checks.append(Check(name, CheckOutcome.PASSED if ok else CheckOutcome.FAILED,
                            expected, actual, None if ok else why))

    numbers = [r.balloon_no for r in rows if r.balloon_no is not None]
    add("balloon_numbers_unique", len(numbers) == len(set(numbers)),
        f"{len(numbers)} distinct numbers", f"{len(set(numbers))} distinct",
        "two balloons carry the same number, so the table and the drawing disagree about which "
        "characteristic is being inspected")

    add("every_characteristic_ballooned", not layout.unplaced,
        f"{len(chars)} placed", f"{len(chars) - len(layout.unplaced)} placed",
        "these characteristics have no balloon, so an inspector reading the drawing would never "
        "find them: " + ", ".join(layout.unplaced))

    collide = overlaps(layout.balloons)
    add("balloons_do_not_overlap", not collide, "0 intersecting pairs",
        f"{len(collide)} intersecting pairs",
        "overlapping balloons cannot be read: " + ", ".join(f"{a}/{b}" for a, b in collide))

    covering = inside_content(layout.balloons, bundle.content_extents)
    add("balloons_clear_of_part", not covering, "0 balloons on the part outline",
        f"{len(covering)} on the outline",
        "these balloons obscure the geometry they describe: " + ", ".join(covering))

    broken_links = [r.cid for r in rows
                    if r.entity_handle and r.source_sha256 != bundle.sha256]
    add("rows_link_to_source_drawing", not broken_links,
        f"{len(rows) - len(manual_rows)} extracted rows carry this drawing's SHA-256",
        f"{len(broken_links)} do not",
        "every extracted characteristic must be traceable to the file it was read from; "
        "these are not: " + ", ".join(broken_links))
    if manual_rows:
        checks.append(Check("reviewer_supplied_rows_without_source_link",
                            CheckOutcome.NOT_VERIFIABLE,
                            "row links to a source entity",
                            f"{len(manual_rows)} reviewer-stated row(s)",
                            "these rows came from a correction file, not from the drawing, so "
                            "they cannot be traced back to an entity or cross-checked against "
                            "geometry: " + ", ".join(r.cid for r in manual_rows)))

    limit_failures: list[str] = []
    ok = all(_check_limits(c, bundle.unit, limit_failures) for c in chars)
    add("limits_independently_recomputed", ok, "every row's limits re-derive consistently",
        "no mismatches" if ok else f"{len(limit_failures)} mismatch(es)",
        "; ".join(limit_failures))

    geo_checked = [c for c in chars if any(
        k.name == "geometry_cross_check" and k.outcome is not CheckOutcome.NOT_VERIFIABLE
        for k in c.checks)]
    geo_disagreeing = [c for c in chars if any(k.name == "geometry_cross_check"
                                              and k.outcome is CheckOutcome.FAILED
                                              for k in c.checks)]
    # A named approval does not erase a disagreement — it reclassifies it. The evidence stays on
    # the characteristic and is reported here, so "accepted" never reads as "never found".
    geo_accepted = [c for c in geo_disagreeing if c.state is ProvenanceState.HUMAN_APPROVED]
    geo_failed = [c for c in geo_disagreeing if c.state is not ProvenanceState.HUMAN_APPROVED]
    if geo_accepted:
        checks.append(Check("geometry_disagreements_accepted_by_reviewer",
                            CheckOutcome.NOT_VERIFIABLE,
                            "disagreements that still need a drawing correction",
                            f"{len(geo_accepted)} accepted",
                            "these written values were approved by a reviewer while the drawn "
                            "geometry still disagrees: "
                            + ", ".join(f"{c.cid}({c.raw_callout!r})" for c in geo_accepted)))
    if chars and not geo_checked:
        checks.append(Check("geometry_cross_check_coverage", CheckOutcome.NOT_VERIFIABLE,
                            "at least one dimension checkable against geometry", "0",
                            "no dimension in this drawing carries usable definition points, so "
                            "nothing has been verified against the drawn geometry"))
    else:
        add("geometry_cross_check_coverage", not geo_failed,
            f"{len(geo_checked)} checked, 0 in disagreement",
            f"{len(geo_checked)} checked, {len(geo_failed)} in disagreement",
            "the written callout and the drawn geometry disagree on: "
            + ", ".join(c.cid for c in geo_failed))

    dims_in_source = next((r.total for r in bundle.census if r.dxftype == "DIMENSION"), 0)
    arc_dims = next((r.total for r in bundle.census if r.dxftype == "ARC_DIMENSION"), 0)
    # A TOLERANCE entity can hold several frame rows; each resolved row is one characteristic,
    # and the count of rows is taken from the entity's own content at ingest.
    frame_rows = next((r.used_for_characteristics for r in bundle.census
                       if r.dxftype == "TOLERANCE"), 0)
    total_dims = dims_in_source + arc_dims + frame_rows
    if any(r.dxftype in ("PDF_TEXT", "OCR_REGION") for r in bundle.census):
        checks.append(Check(
            "census_reconciled", CheckOutcome.NOT_VERIFIABLE, "dimension entities in the source",
            f"{len(chars) - len(manual_rows)} characteristics read from text",
            "a PDF or a scan carries no dimension entities, so there is no independent count of how many "
            "dimensions the drawing has; a dimension drawn without text, or text not read, would "
            "not show up here"))
    else:
        add("census_reconciled", total_dims == len(chars) - len(manual_rows),
            f"{total_dims} dimension entities",
            f"{len(chars) - len(manual_rows)} characteristics from the drawing",
            "dimension entities were read from the drawing without producing a characteristic, or "
            "characteristics exist that no dimension entity supports")

    add("annotations_fully_interpreted", not bundle.uninterpreted,
        "0 uninterpreted annotations", f"{len(bundle.uninterpreted)} escalated",
        "annotation text that looks dimensional was not resolved, so the plan cannot claim the "
        "drawing is fully covered: "
        + ", ".join(repr(u.text) for u in bundle.uninterpreted))

    blocked = [c for c in chars if c.is_blocking]
    add("release_gate_blocking_findings", not blocked, "0 blocked characteristics",
        f"{len(blocked)} blocked",
        "these must be resolved or explicitly approved before the plan is issued: "
        + ", ".join(f"{c.cid}({c.state.value})" for c in blocked))

    dupes: dict[tuple, list[str]] = {}
    for c in chars:
        if c.limits is None and c.nominal is None:
            continue
        # the tolerance is part of the key: flatness 0.02 and parallelism 0.02 | A share kind,
        # nominal and limits but are different requirements, not one dimensioned twice
        dupes.setdefault((c.kind, c.nominal, c.limits, c.tolerance), []).append(c.cid)
    for key, cids in dupes.items():
        if len(cids) < 2:
            continue
        for c in chars:
            if c.cid in cids:
                c.fail("possible_duplicate_callout",
                       f"same kind/nominal/limits as {', '.join(x for x in cids if x != c.cid)} "
                       f"({key[0].value}, {key[1]}, {key[2]}); may be a genuine repeated "
                       "dimension, so it is flagged rather than merged", "warning")

    # Rows were built before the checks above added issues (e.g. possible_duplicate_callout); a row
    # is what the PDF/XLSX print, so it must carry every issue its characteristic now has.
    by_cid = {c.cid: c for c in chars}
    for r in rows:
        c = by_cid[r.cid]
        r.issues = [f"{i.severity}:{i.code}" for i in c.issues]
        r.blocking = c.is_blocking

    for u in bundle.uninterpreted:
        plan_issues.append(Issue("annotation_needs_review", "blocking",
                                 f"{u.text!r} on layer {u.layer}: {u.reason}"))
    for c in chars:
        for i in c.issues:
            if i.severity == "blocking":
                plan_issues.append(Issue(i.code, "blocking", f"{c.cid}: {i.message}"))

    unresolved = [c for c in chars if c.state is ProvenanceState.UNRESOLVED]
    coverage = Coverage(
        dimensions_in_source=total_dims,
        characteristics_extracted=len(chars) - len(manual_rows),
        resolved=len(chars) - len(unresolved),
        unresolved=len(unresolved),
        blocking=len(blocked),
        geometry_cross_checked=len(geo_checked),
        geometry_cross_check_passed=len(geo_checked) - len(geo_disagreeing),
        balloon_assigned=len(numbers),
        balloon_unplaced=len(layout.unplaced),
        annotations_escalated=len(bundle.uninterpreted),
    )

    return InspectionPlan(
        source_document=bundle.path,
        source_sha256=bundle.sha256,
        revision=bundle.revision,
        unit=bundle.unit,
        page_count=bundle.page_count,
        extents=bundle.extents,
        content_extents=bundle.content_extents,
        characteristics=chars,
        rows=rows,
        layout=layout,
        checks=checks,
        issues=plan_issues,
        uninterpreted=list(bundle.uninterpreted),
        coverage=coverage,
        title_block=dict(bundle.title_block),
        notes=list(bundle.notes),
        revision_source=bundle.revision_source,
        warnings=list(bundle.warnings),
        feature_support=dict(bundle.feature_support),
        census=[asdict(r) for r in bundle.census],
    )


def _parse_tolerance_spec(spec) -> object:
    """Build a tolerance object from an override file's plain dict, refusing to invent one."""
    if spec is None:
        return NoTolerance()
    if isinstance(spec, str):
        p = parse_callout(spec)
        # A string the parser cannot turn into a tolerance must not become "no tolerance": that
        # would convert an unreadable override into a claim that the drawing states none.
        if p.unresolved_reason or isinstance(p.tolerance, NoTolerance):
            raise ValueError(f"override tolerance {spec!r} is not interpretable "
                             f"({p.unresolved_reason or 'no tolerance in that text'})")
        return p.tolerance
    kind = spec.get("kind", "symmetric")
    if kind in ("none", "no_tolerance"):
        return NoTolerance()
    if kind == "symmetric":
        return Symmetric(plus_minus=float(spec["plus_minus"]))
    if kind == "deviations":
        return Deviations(upper=float(spec["upper"]), lower=float(spec["lower"]))
    if kind == "limit_pair":
        return LimitPair(maximum=float(spec["maximum"]), minimum=float(spec["minimum"]))
    raise ValueError(f"override tolerance kind {kind!r} is not supported")


def apply_overrides(bundle, path: str | Path) -> list[str]:
    """Apply an engineer's correction file to an ingested bundle.

    Every mutation is recorded with the actor that made it. An override never edits the original
    callout text and never implies the drawing stated something it did not: values a reviewer
    supplies carry the user-supplied state, and a dismissed annotation is kept as a note.
    """
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    chars: list[Characteristic] = bundle.characteristics
    by_cid = {c.cid: c for c in chars}
    actor = f"override:{p.name}"
    applied: list[str] = []

    for entry in data.get("characteristics", []):
        c = by_cid.get(entry.get("cid"))
        if c is None:
            raise ValueError(f"override references unknown characteristic {entry.get('cid')!r}")
        if "kind" in entry:
            c.kind = CharKind(entry["kind"])
            c.record(actor, "kind_overridden", f"kind set to {c.kind.value}")
        if "nominal" in entry:
            raw_nominal = entry["nominal"]
            # An explicit null is a statement, not an omission: a limit-pair callout has no
            # nominal, and clearing one is a legitimate correction.
            c.nominal = None if raw_nominal is None else float(raw_nominal)
            c.state = ProvenanceState.USER_SPECIFIED
            c.record(actor, "nominal_overridden",
                     "nominal cleared (limit pair states no nominal)" if c.nominal is None
                     else f"nominal set to {c.nominal}")
        if "tolerance" in entry:
            c.tolerance = _parse_tolerance_spec(entry["tolerance"])
            c.state = ProvenanceState.USER_SPECIFIED
            c.limits = resolve_limits(c.nominal, c.tolerance, c.unit)
            c.record(actor, "tolerance_overridden",
                     f"tolerance set to {describe_tolerance(c.tolerance, c.limits)}")
        if entry.get("reject"):
            c.state = ProvenanceState.REJECTED
            c.review = ReviewStatus.REJECTED
            reason = entry.get("reason") or "no reason given"
            c.fail("rejected_by_reviewer", reason, "blocking")
            c.record(actor, "rejected", reason)
        elif entry.get("approve"):
            reviewer = entry.get("reviewer") or "<unnamed>"
            c.state = ProvenanceState.HUMAN_APPROVED
            c.review = ReviewStatus.APPROVED
            c.issues = [i for i in c.issues if i.severity != "blocking"]
            c.limits = resolve_limits(c.nominal, c.tolerance, c.unit)
            c.record(actor, "approved",
                     f"approved by {reviewer}; blocking findings marked accepted, not corrected")
        else:
            c.review = ReviewStatus.NEEDS_REVIEW
            c.limits = resolve_limits(c.nominal, c.tolerance, c.unit)
        if c.limits is not None:
            # The reviewer supplied definite limits, so the earlier "no limits" finding no
            # longer describes this row and must not linger as a stale warning.
            c.issues = [i for i in c.issues if i.code != "no_limits"]
        applied.append(c.cid)

    remaining = []
    for u in bundle.uninterpreted:
        entry = next((e for e in data.get("annotations", [])
                      if e.get("text") == u.text or e.get("handle") == u.entity_handle), None)
        if entry is None:
            remaining.append(u)
            continue
        action = entry.get("action", "dismiss")
        reviewer = entry.get("reviewer") or "<unnamed>"
        if action == "dismiss":
            bundle.notes.append(
                f"annotation {u.text!r} dismissed as non-dimensional by {reviewer}"
                f" ({entry.get('reason', 'no reason given')})")
        elif action == "as_characteristic":
            parsed = parse_callout(entry.get("callout", u.text), bundle.unit)
            tol = _parse_tolerance_spec(entry["tolerance"]) if "tolerance" in entry \
                else parsed.tolerance
            cid = f"CH-{len(chars) + 1:03d}"
            new = Characteristic(
                cid=cid,
                kind=CharKind(entry["kind"]) if "kind" in entry else parsed.kind,
                raw_callout=u.text,
                unit=bundle.unit,
                nominal=float(entry["nominal"]) if "nominal" in entry else parsed.nominal,
                tolerance=tol,
                state=ProvenanceState.USER_SPECIFIED,
                review=ReviewStatus.APPROVED if entry.get("approve") else ReviewStatus.NEEDS_REVIEW,
            )
            new.limits = resolve_limits(new.nominal, new.tolerance, bundle.unit)
            new.record(actor, "annotation_converted",
                       f"reviewer {reviewer} stated this text means "
                       f"{new.kind.value} {new.nominal} {describe_tolerance(tol, new.limits)}; "
                       "no definition points exist, so it cannot be cross-checked")
            if entry.get("approve"):
                new.state = ProvenanceState.HUMAN_APPROVED
            chars.append(new)
            by_cid[cid] = new
            applied.append(cid)
        else:
            raise ValueError(f"annotation action {action!r} is not supported")
        applied.append(f"annotation:{u.text}")
    bundle.uninterpreted = remaining

    return applied
