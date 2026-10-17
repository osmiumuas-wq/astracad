"""DXF ingestion route.

Vector DXF carries real geometry and real text, so this route needs no OCR and can cross-check
every callout against the dimension's own definition points. That cross-check is the point:
the number written on a drawing and the number the drawing is actually drawn to are two
independent claims, and a disagreement between them is an engineering finding, not a rounding
detail.

Nothing in the modelspace is dropped without being recorded in the entity census, so an
unsupported annotation shows up as a gap rather than as an empty row.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf import bbox

from .. import iso286
from ..callouts import CalloutParse, _NUM, normalise, parse_callout
from ..model import (
    CharKind,
    Characteristic,
    Check,
    CheckOutcome,
    NoTolerance,
    ProvenanceState,
    ReviewStatus,
    SourceLocation,
    UninterpretedAnnotation,
)
from ..units import INSUNITS_TO_UNIT, UnitError

# group 70 low bits, as emitted by AutoCAD/SOLIDWORKS/LibreCAD exporters.
_DIMTYPE_KIND = {
    0: CharKind.LINEAR,     # rotated (horizontal/vertical per the rotation attribute)
    1: CharKind.LINEAR,     # aligned
    2: CharKind.ANGULAR,    # angular: 3 points
    3: CharKind.DIAMETER,
    4: CharKind.RADIUS,
    5: CharKind.ANGULAR,    # angular: 2 lines
    6: CharKind.LINEAR,     # ordinate — a linear distance from an origin
}

# A loose text entity that looks dimensional: two bare numbers (the ambiguous form the project
# report warns about) or one number plus a tolerance sign. Escalated, never auto-extracted,
# because free text has no definition points to check it against.
#
# The token must stand alone. Without that, the aluminium designation "6061-T6" reads as two
# numbers and every material note lands in the review queue — and a review queue full of
# non-findings is a queue nobody trusts. A colon separator is deliberately excluded: "1:50" is
# a scale, not a dimension written in an ambiguous notation.
_NUM_TOK = rf"(?<![0-9.]){_NUM}(?![A-Za-z0-9])"
_CALLOUT_LIKE = re.compile(
    rf"{_NUM_TOK}[\s,]+{_NUM_TOK}"            # two bare numbers, no separating notation
    rf"|{_NUM_TOK}\s*(?:±|[-+]\s*{_NUM_TOK}|/)",  # one number carrying tolerance marks
    re.I)

_GEOMETRY_TYPES = frozenset({
    "LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "RAY",
    "XLINE", "SOLID", "TRACE", "3DFACE", "HATCH", "INSERT",
})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pt(e) -> tuple[float, float]:
    return (float(e.x), float(e.y))


def _has(e, name: str) -> bool:
    return e.dxf.hasattr(name)


def _vec(a, b) -> tuple[float, float]:
    return (b[0] - a[0], b[1] - a[1])


def _dist(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _angle_between(center, p1, p2) -> float:
    v1, v2 = _vec(center, p1), _vec(center, p2)
    a1 = math.degrees(math.atan2(v1[1], v1[0]))
    a2 = math.degrees(math.atan2(v2[1], v2[0]))
    return abs((a1 - a2) % 360.0)


@dataclass
class EntityCensusRow:
    dxftype: str
    total: int
    used_for_characteristics: int
    note: str


@dataclass
class DrawingBundle:
    """Everything one ingestion pass learned about one drawing file."""
    path: str
    sha256: str
    revision: str
    unit: str
    extents: tuple[float, float, float, float]        # minx, miny, maxx, maxy (drawing units)
    content_extents: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    notes: list[str] = field(default_factory=list)
    page_count: int = 1
    characteristics: list[Characteristic] = field(default_factory=list)
    uninterpreted: list[UninterpretedAnnotation] = field(default_factory=list)
    census: list[EntityCensusRow] = field(default_factory=list)
    title_block: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    feature_support: dict[str, str] = field(default_factory=dict)
    dxf_version: str = ""
    revision_source: str = "default - not stated by the drawing"
    dimstyle_notes: list[str] = field(default_factory=list)


def resolve_unit(doc, declared: str | None, warnings: list[str]) -> str:
    """The unit is never assumed. INSUNITS=0 means the drawing does not say, and that is a
    blocker for inspection rather than a licence to guess."""
    if declared:
        return declared
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    unit = INSUNITS_TO_UNIT.get(code)
    if unit:
        return unit
    raise UnitError(
        "the drawing does not declare its units (DXF $INSUNITS = 0/unspecified). "
        "Pass --units explicitly; interpreting a number as millimetres without the source "
        "saying so would be an invention.")


def _text_of(entity) -> str:
    if entity.dxftype() == "MTEXT":
        return entity.text
    return entity.dxf.text


def _anchor(entity) -> tuple[float, float]:
    for attr in ("insert", "location", "defpoint", "align_point"):
        try:
            if entity.dxf.hasattr(attr):
                v = getattr(entity.dxf, attr)
                return (float(v.x), float(v.y))
        except (AttributeError, TypeError, ValueError):
            continue
    return (0.0, 0.0)


def _measurable_extents(entities) -> tuple[tuple[float, float, float, float], list[str]]:
    """Bounding box of the entities this build can measure, and the names of the ones it could not.

    ezdxf raises rather than returning a box for entities it cannot disassemble — LibreCAD writes
    LEADERs that carry no vertex coordinates at all — and one unreadable arrow must not cost a real
    drawing its whole layout. So the single pass is tried first (its numbers are what a well-formed
    file has always yielded) and only a failure drops to measuring entity by entity. The extents
    then cover everything measurable, and what was left out is named instead of quietly dropped."""
    entities = list(entities)
    try:
        box = bbox.extents(entities)
    except ValueError:
        box = None
    if box is not None and box.has_data:
        return (box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y), []

    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    unreadable: list[str] = []
    for e in entities:
        try:
            one = bbox.extents([e])
        except ValueError:
            unreadable.append(f"{e.dxftype()} (handle {e.dxf.handle})")
            continue
        if not one.has_data:
            continue
        min_x = min(min_x, one.extmin.x)
        min_y = min(min_y, one.extmin.y)
        max_x = max(max_x, one.extmax.x)
        max_y = max(max_y, one.extmax.y)
    if min_x == math.inf:
        raise ValueError("DXF modelspace has no geometric extents; balloon layout is impossible")
    return (min_x, min_y, max_x, max_y), unreadable


def _cross_check(c: Characteristic, parsed: CalloutParse, geometry: float | None,
                 unit: str, printed_precision: float) -> None:
    """Compare the written callout against the drawn geometry. Two independent sources either
    agree, or the characteristic goes to review. Agreement is never assumed."""
    if geometry is None:
        c.checks.append(Check("geometry_cross_check", CheckOutcome.NOT_VERIFIABLE,
                              "callout matches drawn geometry", "-",
                      "the dimension carries no usable definition points, so there is no "
                      "second source to check the written value against"))
        return
    if parsed.nominal is None:
        c.checks.append(Check("geometry_cross_check", CheckOutcome.NOT_VERIFIABLE,
                              "callout matches drawn geometry", f"geometry={geometry:.6f}",
                      "the callout states no single nominal (a limit pair, or a measured "
                      "placeholder), so there is nothing to compare the geometry with"))
        return

    band = 0.0
    tol = parsed.tolerance
    bound = "written tolerance band"
    if not isinstance(tol, NoTolerance):
        from ..limits import deviation_band, resolve_limits
        # An ISO 286-2 band is size-dependent, so its width comes from the resolved limits when
        # the published table states them; deviation_band() has no nominal to look a bracket up
        # with and would report a zero-wide zone, turning a legal bore into a false finding.
        limits = resolve_limits(parsed.nominal, tol, unit)
        band = (limits[1] - limits[0]) if limits is not None else deviation_band(tol, unit)
    else:
        # A callout that states no tolerance offers only the resolution it was printed at. Demanding
        # agreement to a micron would call a drawing self-contradictory because its author showed
        # two decimals; anything wider than half a unit of that last digit is still a contradiction.
        bound = "printed resolution"
        band = printed_precision
    # A drawing is drawn to nominal, so the geometry may legally sit anywhere within the
    # tolerance band. Outside it, the text and the picture disagree and that is a finding.
    slack = max(band, abs(parsed.nominal) * 1e-9 + 1e-6)
    delta = geometry - parsed.nominal
    ok = abs(delta) <= slack
    c.checks.append(Check(
        "geometry_cross_check",
        CheckOutcome.PASSED if ok else CheckOutcome.FAILED,
        f"{parsed.nominal:.6f} +/- {slack:.6f}",
        f"{geometry:.6f}",
        None if ok else (
            f"the drawn geometry is outside the {bound}; either the drawing was "
            "not modelled to this dimension or the callout is stale"),
        delta))
    if not ok:
        c.fail("geometry_callout_disagreement",
               f"callout says {parsed.nominal} but the geometry measures {geometry:.6f} "
               f"({delta:+.6f} outside the band)", "blocking")


def _block_callout(doc, entity) -> str:
    """The text a dimension actually shows on the sheet, read from the dimension's own block.

    An author who does not override the text gets the measured value printed - with the drawing's
    rounding and its decimal separator - and CAD writes that into the block the dimension references,
    leaving `dxf.text` on the modelspace entity empty. Reading only the entity makes a real drawing
    look like twenty dimensions that state nothing."""
    name = entity.dxf.geometry if entity.dxf.hasattr("geometry") else ""
    if not name or name not in doc.blocks:
        return ""
    parts = []
    for e in doc.blocks.get(name):
        t = e.dxftype()
        if t == "MTEXT":
            parts.append(e.plain_text())
        elif t in ("TEXT", "ATTRIB"):
            parts.append(e.dxf.text)
    return normalise(" ".join(p for p in parts if p))


def _callout_text(doc, entity) -> tuple[str, str]:
    """What the dimension states, and where that was read from.

    The override wins, because it is what the author typed. Otherwise the rendered block text is the
    drawing's own statement of the value. When neither carries anything, the dimension states no size
    and that is reported as a gap rather than quietly read as a measured placeholder."""
    override = ""
    if entity.dxf.hasattr("text"):
        override = (entity.dxf.text or "").strip()
    if override and override != "<>":
        return override, "the dimension's text override"
    shown = _block_callout(doc, entity)
    if shown:
        return shown, f"the dimension's rendered text in block {entity.dxf.geometry}"
    return "", ""


_COMMA_IN_NUMBER = re.compile(r"(?<=\d),(?=\d)")


def _comma_variants(text: str) -> list[tuple[str, str]]:
    """The readings a digit-comma-digit token admits, in the two conventions CAD files use for it.

    '42,43' is 42.43 mm to a European author and 4243 mm to none; '1,500' is 1500 mm to an
    American one and 1.5 m to no one who declared millimetres. Both are offered and the drawing
    decides - this function only lists what the token could mean."""
    if not _COMMA_IN_NUMBER.search(text):
        return []
    out = [("decimal comma", _COMMA_IN_NUMBER.sub(".", text))]
    grouped = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    if grouped != text:
        out.append(("thousands separator", grouped))
    return out


def _printed_precision(text: str) -> float:
    """Half a unit of the last decimal the drawing prints: the window in which a written size and a
    measured one are the same size. A callout that prints two decimals rounds, so 42.43 does state
    42.4264 - and does not state 42.4 or 42.44."""
    m = re.search(r"[.,](\d+)", text)
    decimals = len(m.group(1)) if m else 0
    return 0.5 * 10 ** -decimals


def _geometry_candidates(e, kind: CharKind) -> list[tuple[str, float]]:
    """Every distance the dimension's own data supports, each named by the route that produced it.

    The measurement field, the distance between the extension points and that pair's horizontal and
    vertical projections are separate claims, and a real drawing does not always keep them
    consistent: the LibreCAD sample has one dimension whose measurement field says 0 while its
    printed text says 30 and its points stand 30 apart vertically. Which claim the author meant is
    answered by the callout matching exactly one of them."""
    out: list[tuple[str, float]] = []
    try:
        measurement = e.get_measurement()
    except Exception:
        measurement = None
    if isinstance(measurement, (int, float)):
        out.append(("the DXF measurement value", float(measurement)))
    elif measurement is not None:  # an ordinate dimension's vector
        for axis in ("x", "y"):
            v = getattr(measurement, axis, None)
            if v is not None:
                out.append((f"the measured {axis} ordinate", float(v)))
    from_points = _measure_from_points(e, kind)
    if from_points is not None:
        out.append(("the definition points for its declared type", float(from_points)))
    if kind is CharKind.LINEAR and e.dxf.hasattr("defpoint2") and e.dxf.hasattr("defpoint3"):
        a, b = _pt(e.dxf.defpoint2), _pt(e.dxf.defpoint3)
        out.append(("the distance between the extension points", math.hypot(b[0] - a[0], b[1] - a[1])))
        out.append(("the horizontal distance between the extension points", abs(b[0] - a[0])))
        out.append(("the vertical distance between the extension points", abs(b[1] - a[1])))
    return out


def _agreeing_route(value: float, tol: float,
                    candidates: list[tuple[str, float]]) -> tuple[str, float] | None:
    """The single route that measures this value, or None when none or several distinct ones do.

    Several labels on one number are agreement, not ambiguity: a horizontal 30 mm span is the same
    distance seen as the measurement field, the span and the projection. Two different numbers
    inside the window would mean the callout does not say which was meant, and nothing is decided."""
    hits: dict[float, list[str]] = {}
    for label, v in candidates:
        if abs(v - value) <= tol:
            hits.setdefault(round(v, 9), []).append(label)
    if len(hits) != 1:
        return None
    value_hit, labels = next(iter(hits.items()))
    return " / ".join(labels), value_hit


def _build_characteristic(cid: str, entity, parsed: CalloutParse, geometry: float | None,
                          doc_sha: str, path: str, revision: str, unit: str,
                          detected_kind: CharKind, kind_from_text: bool,
                          callout_text: str, printed_precision: float) -> Characteristic:
    points = []
    for attr in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5", "defpoint6"):
        if entity.dxf.hasattr(attr):
            points.append(_pt(getattr(entity.dxf, attr)))
    real = [q for q in points if q != (0.0, 0.0)]
    if real:
        anchor = (sum(q[0] for q in real) / len(real), sum(q[1] for q in real) / len(real))
    else:
        anchor = _anchor(entity)

    source = SourceLocation(
        document=path,
        document_sha256=doc_sha,
        revision=revision,
        page=1,
        entity_handle=entity.dxf.handle,
        entity_type=entity.dxftype(),
        layer=entity.dxf.layer,
        definition_points=points,
        anchor_x=anchor[0],
        anchor_y=anchor[1],
    )

    from ..limits import resolve_limits
    limits = resolve_limits(parsed.nominal, parsed.tolerance, unit)

    c = Characteristic(
        cid=cid,
        kind=parsed.kind if kind_from_text else detected_kind,
        raw_callout=callout_text,
        unit=unit,
        nominal=parsed.nominal,
        tolerance=parsed.tolerance,
        count=parsed.count,
        state=ProvenanceState.PARSED if parsed.resolved else ProvenanceState.UNRESOLVED,
        review=ReviewStatus.DETECTED if parsed.resolved else ReviewStatus.NEEDS_REVIEW,
        source=source,
        geometry_value=geometry,
        limits=limits,
    )
    c.record("extractor", "extracted",
             f"{entity.dxftype()} handle {entity.dxf.handle} on layer {entity.dxf.layer}")
    for n in parsed.notes:
        c.record("parser", "noted", n)
    if not parsed.resolved:
        c.fail("unresolved_callout", parsed.unresolved_reason or "could not interpret the callout",
               "blocking")
    if limits is None and parsed.kind is not CharKind.ANGULAR:
        c.fail("no_limits", "no definite upper/lower limits could be derived from the stated "
                            "tolerance; inspection cannot pass/fail this characteristic",
               "warning")
    _cross_check(c, parsed, geometry, unit, printed_precision)
    return c


def _frame_characteristics(e, next_cid, doc_sha: str, path: str, revision: str, unit: str,
                           uninterpreted: list) -> list[Characteristic]:
    """One characteristic per resolved row of a TOLERANCE entity (feature control frame).

    A frame has no definition points and states a zone, not a size, so there is nothing in the
    drawing to cross-check it against: the check is recorded as not verifiable. A row that does
    not parse is escalated with its reason instead of becoming a guessed characteristic."""
    from ..gdt import parse_dxf_tolerance
    content = e.dxf.get("content", "") or ""
    ax, ay = _anchor(e)
    out = []
    rows = parse_dxf_tolerance(content)
    if not rows:
        uninterpreted.append(UninterpretedAnnotation(
            document=path, entity_handle=e.dxf.handle, layer=e.dxf.layer, text=content,
            reason="feature control frame with no readable row", anchor_x=ax, anchor_y=ay))
    for i, fr in enumerate(rows):
        if not fr.resolved:
            uninterpreted.append(UninterpretedAnnotation(
                document=path, entity_handle=e.dxf.handle, layer=e.dxf.layer, text=fr.raw,
                reason=f"feature control frame row {i + 1} not resolved: {fr.unresolved_reason}",
                anchor_x=ax, anchor_y=ay - 4.0 * i))
            continue
        tol = fr.tolerance
        c = Characteristic(
            cid=next_cid(), kind=CharKind.GEOMETRIC, raw_callout=fr.raw, unit=unit,
            nominal=None, tolerance=tol, state=ProvenanceState.PARSED,
            review=ReviewStatus.DETECTED,
            source=SourceLocation(document=path, document_sha256=doc_sha, revision=revision,
                                  page=1, entity_handle=e.dxf.handle, entity_type="TOLERANCE",
                                  layer=e.dxf.layer, anchor_x=ax, anchor_y=ay - 4.0 * i),
            limits=(0.0, tol.zone))
        c.record("extractor", "extracted",
                 f"TOLERANCE handle {e.dxf.handle} row {i + 1} on layer {e.dxf.layer}")
        for n in fr.notes:
            c.record("parser", "noted", n)
        c.checks.append(Check("geometry_cross_check", CheckOutcome.NOT_VERIFIABLE,
                              "callout matches drawn geometry", "-",
                              "a feature control frame states a zone, not a size; the drawing "
                              "holds no second value to compare it with"))
        if tol.modifier in ("MMC", "LMC"):
            c.fail("gdt_bonus_tolerance_not_applied",
                   f"{tol.symbol} at {tol.modifier}: the limit shown is the stated zone only; bonus "
                   "tolerance from the produced feature size must be added at inspection", "warning")
        out.append(c)
    return out


def read_dxf(path: str | Path, declared_unit: str | None = None,
             revision: str | None = None) -> DrawingBundle:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"drawing not found: {p}")
    doc_sha = sha256_file(p)

    try:
        doc = ezdxf.readfile(str(p))
    except Exception as ex:  # malformed CAD files are untrusted input
        raise ValueError(f"DXF could not be parsed: {type(ex).__name__}: {ex}") from ex

    warnings: list[str] = []
    unit = resolve_unit(doc, declared_unit, warnings)
    msp = doc.modelspace()

    title_block = _read_title_block(msp, doc)
    # A revision letter printed on a report is a claim about which drawing this plan describes.
    # It is taken from the drawing when the drawing says, from the caller when the caller said,
    # and otherwise recorded as this build's default rather than presented as read content.
    if revision:
        revision, revision_source = revision.strip() or "A", "user-specified"
    elif title_block.get("revision"):
        revision, revision_source = title_block["revision"], "title block"
    else:
        revision, revision_source = "A", ("default - the drawing states no revision; confirm "
                                          "before issuing")
        warnings.append("revision defaults to A: no revision value was recognised in the drawing")

    dims = [e for e in msp if e.dxftype() in ("DIMENSION", "ARC_DIMENSION")]
    characteristics: list[Characteristic] = []
    uninterpreted: list[UninterpretedAnnotation] = []
    census: dict[str, EntityCensusRow] = {}
    cid_n = 0

    def next_cid() -> str:
        nonlocal cid_n
        cid_n += 1
        return f"CH-{cid_n:03d}"

    for e in dims:
        row = census.setdefault(e.dxftype(), EntityCensusRow(e.dxftype(), 0, 0, ""))
        row.total += 1
        low_type = int(getattr(e, "dimtype", 0)) & 7
        detected_kind = _DIMTYPE_KIND.get(low_type, CharKind.UNKNOWN)
        if e.dxftype() == "ARC_DIMENSION":
            detected_kind = CharKind.RADIUS
        candidates = _geometry_candidates(e, detected_kind)

        text, text_from = _callout_text(doc, e)
        parsed = parse_callout(text, unit)
        if text_from:
            parsed.notes.append("callout read from " + text_from)
        else:
            parsed.unresolved_reason = ("the dimension states no size: it has no text override and "
                                        "its dimension block carries no text, so nothing was written "
                                        "to inspect against")
            warnings.append(f"{e.dxftype()} handle {e.dxf.handle}: no callout text on the entity "
                            "and none in its dimension block")

        printed_precision = _printed_precision(text)
        if not parsed.resolved and _COMMA_IN_NUMBER.search(text):
            # A digit-comma-digit token is two conventions at once. The drawing settles which one
            # this file uses only if one reading, and no other, is a distance it actually contains.
            matched = []
            for label, variant in _comma_variants(text):
                alt = parse_callout(variant, unit)
                if alt.nominal is None or not alt.resolved:
                    continue
                route = _agreeing_route(alt.nominal, _printed_precision(variant), candidates)
                if route:
                    matched.append((label, alt, variant, route))
            if len(matched) == 1:
                label, alt, variant, route = matched[0]
                alt.raw, alt.normalised = parsed.raw, text
                alt.notes = parsed.notes + [
                    f"{label}: '{text}' read as {alt.nominal:g} - {route[0]} on this drawing is "
                    f"{route[1]:.6f}, which rounds to the {text} printed"]
                printed_precision = _printed_precision(variant)
                parsed = alt
            else:
                readings = ", ".join(f"as a {label} it is "
                                     f"{parse_callout(variant, unit).nominal:g}"
                                     for label, variant in _comma_variants(text))
                parsed.notes.append(
                    f"decimal separator left unresolved: {readings} and none of those values is a "
                    "distance this drawing's geometry measures, so the token needs a person")
        kind_from_text = parsed.kind is not CharKind.UNKNOWN and parsed.kind != CharKind.LINEAR

        geometry = None
        try:
            geometry = float(e.get_measurement())
        except Exception as ex:
            geometry = None
            warnings.append(f"{e.dxftype()} handle {e.dxf.handle}: get_measurement() failed "
                            f"({type(ex).__name__}: {ex}); falling back to definition points")

        if geometry is None and detected_kind is not CharKind.UNKNOWN:
            geometry = _measure_from_points(e, detected_kind)
            if geometry is not None:
                warnings.append(f"handle {e.dxf.handle}: geometric value computed from "
                                f"definition points instead of the DXF measurement cache")
            elif parsed.resolved:
                parsed.notes.append("geometric value could not be computed; no cross-check available")

        # The declared measurement is the drawing's own claim about the distance, but it is not
        # always the claim its printed size refers to: a dimension declared horizontal can stand
        # between two points that differ only vertically. Where exactly one route measures what the
        # callout prints, that route is what the picture says, and the disagreement is recorded as
        # a question about orientation rather than as a size that was drawn wrong.
        projection_warning = None
        if parsed.nominal is not None and detected_kind is not CharKind.ANGULAR:
            agree = _agreeing_route(parsed.nominal, printed_precision, candidates)
            if agree is not None and (geometry is None or abs(geometry - agree[1])
                                      > printed_precision):
                if geometry is not None:
                    projection_warning = (
                        f"the printed size {parsed.nominal:g} measures {agree[1]:.4f} as "
                        f"{agree[0]}, while the dimension's declared value is {geometry:.4f}: the "
                        "file's projection flag and its printed number disagree, so confirm which "
                        "distance the author meant")
                    parsed.notes.append("geometry route reconciled: " + projection_warning)
                geometry = agree[1]

        # An angular DIMENSION's measurement follows a direction convention the text may not.
        if detected_kind is CharKind.ANGULAR and geometry is not None and parsed.nominal is not None:
            readings = {"as-measured": geometry, "reflex": 360.0 - geometry,
                        "supplement": abs(180.0 - geometry)}
            matched = next((lbl for lbl, val in readings.items()
                            if abs(val - parsed.nominal) <= max(1e-6, abs(parsed.nominal) * 1e-9)),
                           None)
            if matched:
                if matched != "as-measured":
                    parsed.notes.append(
                        f"angular reading reconciled via the '{matched}' convention: the DXF "
                        f"measurement is {geometry:.4f} deg, the callout states "
                        f"{parsed.nominal} deg; the two are the same angle counted differently")
                geometry = readings[matched]
            else:
                parsed.notes.append(
                    "angular dimension convention check: the drawn angle is "
                    f"{geometry:.4f} deg, not {parsed.nominal}; recorded as an unresolved "
                    "orientation question rather than silently converted")
                parsed.unresolved_reason = parsed.unresolved_reason or (
                    "angular callout does not match any reading of the drawn angle "
                    f"({geometry:.4f}); confirm which angle the author meant")
                geometry = None  # do not cross-check two numbers that are not the same quantity

        c = _build_characteristic(next_cid(), e, parsed, geometry, doc_sha, str(p),
                                  revision, unit, detected_kind, kind_from_text, text,
                                  printed_precision)
        if projection_warning:
            c.fail("geometry_projection_undocumented", projection_warning, "warning")
        characteristics.append(c)
        row.used_for_characteristics += 1

    # Loose text that reads like a dimension: escalate for review, never invent a characteristic.
    notes: list[str] = []
    for e in msp:
        t = e.dxftype()
        if t in ("DIMENSION", "ARC_DIMENSION"):
            continue
        row = census.setdefault(t, EntityCensusRow(t, 0, 0, ""))
        row.total += 1
        if t == "TOLERANCE":
            for c in _frame_characteristics(e, next_cid, doc_sha, str(p), revision, unit,
                                            uninterpreted):
                characteristics.append(c)
                row.used_for_characteristics += 1
            row.note = "feature control frames; one characteristic per resolved frame row"
            continue
        if t not in ("TEXT", "MTEXT"):
            continue
        raw = _text_of(e)
        if not raw:
            continue
        norm = normalise(raw)
        ax, ay = _anchor(e)
        if _CALLOUT_LIKE.search(norm):
            uninterpreted.append(UninterpretedAnnotation(
                document=str(p), entity_handle=e.dxf.handle, layer=e.dxf.layer, text=raw,
                reason="free text that resembles a dimension; it has no definition points, so it "
                       "cannot be cross-checked against geometry and is not auto-accepted",
                anchor_x=ax, anchor_y=ay))
            row.used_for_characteristics += 1
        elif len(norm) > 3 and re.search(r"\d", norm):
            notes.append(norm)

    extents, unreadable = _measurable_extents(msp)
    for what in unreadable:
        warnings.append(f"{what} could not be measured, so the drawing extents cover the entities "
                        "that could - it is not a claim that every entity was included")

    # Balloons ring the part, not the title block: content extents come from geometry entities
    # only, so text placed far from the outline does not stretch the balloon band.
    geo_only = [e for e in msp if e.dxftype() in _GEOMETRY_TYPES]
    content_extents, _ = _measurable_extents(geo_only) if geo_only else extents
    # ezdxf cannot measure a TOLERANCE entity, so a frame placed beside the part would sit outside
    # the balloon ring and get covered. Its box is estimated from its rows and cells.
    for e in msp.query("TOLERANCE"):
        from ..gdt import parse_dxf_tolerance
        rows = parse_dxf_tolerance(e.dxf.get("content", "") or "") or [None]
        cells = max((2 + len(r.tolerance.datums)) if r and r.tolerance else 2 for r in rows)
        x, y = float(e.dxf.insert.x), float(e.dxf.insert.y)
        w, h = cells * 8.0 + 8.0, 4.0 * len(rows)
        cx0, cy0, cx1, cy1 = content_extents
        content_extents = (min(cx0, x), min(cy0, y - h), max(cx1, x + w), max(cy1, y))

    feature_support = {
        "dimension.linear": "supported",
        "dimension.diameter": "supported",
        "dimension.radius": "supported",
        "dimension.angular": "supported, with orientation convention checks",
        "dimension.ordinate": "partial - kind mapped, measurement not cross-checked",
        "tolerance.symmetric_deviations": "supported",
        "tolerance.limit_pair": "supported",
        "tolerance.iso_fit_class": iso286.support_statement(),
        "tolerance.thread_designation": "NOT supported - left unresolved",
        "gdt.feature_control_frame": "supported for TOLERANCE entities: symbol, zone (⌀), MMC/LMC/RFS, datums; one row per frame row; MMC/LMC bonus not computed",
        "gdt.datum_reference": "datum letters read from frames; datum feature symbols (triangles) not linked",
        "surface_finish_symbol": "NOT supported in this milestone",
        "raster_ocr": "NOT used by this route (vector DXF)",
    }

    return DrawingBundle(
        path=str(p), sha256=doc_sha, revision=revision, revision_source=revision_source,
        unit=unit, extents=extents,
        content_extents=content_extents, notes=notes,
        characteristics=characteristics, uninterpreted=uninterpreted,
        census=sorted(census.values(), key=lambda r: r.dxftype),
        title_block=title_block, warnings=warnings, feature_support=feature_support,
        dxf_version=doc.dxfversion,
    )


def _measure_from_points(e, kind: CharKind) -> float | None:
    """Independent geometric evaluation from definition points, used when the DXF measurement
    cache is missing (which is common for exporter-written files)."""
    pts = {}
    for attr in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5", "defpoint6"):
        if e.dxf.hasattr(attr):
            pts[attr] = _pt(getattr(e.dxf, attr))
    if kind is CharKind.DIAMETER and "defpoint" in pts and "defpoint4" in pts:
        return 2 * _dist(pts["defpoint4"], pts["defpoint"])
    if kind is CharKind.RADIUS:
        if e.dxftype() == "ARC_DIMENSION" and {"defpoint4", "defpoint2"} <= pts.keys():
            return _dist(pts["defpoint4"], pts["defpoint2"])
        if "defpoint" in pts and "defpoint4" in pts:
            return _dist(pts["defpoint"], pts["defpoint4"])
    if kind is CharKind.ANGULAR and {"defpoint2", "defpoint3", "defpoint4"} <= pts.keys():
        return _angle_between(pts["defpoint4"], pts["defpoint2"], pts["defpoint3"])
    if kind is CharKind.LINEAR and "defpoint2" in pts and "defpoint3" in pts:
        rot = float(e.dxf.rotation) if e.dxf.hasattr("rotation") else 0.0
        if e.dxf.hasattr("angle") and float(e.dxf.angle):
            rot = float(e.dxf.angle)
        a, b = pts["defpoint2"], pts["defpoint3"]
        if rot in (0.0, 180.0):
            return abs(b[0] - a[0])
        if rot in (90.0, 270.0):
            return abs(b[1] - a[1])
        return _dist(a, b)
    return None


def _read_title_block(msp, doc) -> dict[str, str]:
    """Title-block text is pulled opportunistically. Whatever is found is reported; absence is
    reported as absence rather than filled with a placeholder."""
    out: dict[str, str] = {}
    labels = {
        "part name": "part_name", "title": "part_name", "drawing number": "drawing_number",
        "drawn no": "drawing_number", "scale": "scale", "material": "material",
        "revision": "revision", "rev": "revision", "units": "units",
        "sheet": "sheet", "date": "date",
    }
    texts = [e for e in msp if e.dxftype() in ("TEXT", "MTEXT")]
    for i, e in enumerate(texts):
        raw = normalise(_text_of(e)).lower()
        for label, key in labels.items():
            if raw.startswith(label) and key not in out:
                value = _text_of(e)[len(label):].strip(" :")
                if not value and i + 1 < len(texts):
                    value = _text_of(texts[i + 1]).strip()
                if value:
                    out[key] = value
    if "drawing_number" not in out:
        out["_note"] = "no title-block drawing number recognised; recorded as absent"
    return out
