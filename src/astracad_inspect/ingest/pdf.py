"""Vector-PDF ingestion route.

A vector PDF (a CAD "Save as PDF") keeps its text as text, so callouts can be read without OCR.
It does NOT keep dimension entities: there are no definition points, no dimension type and no
link between a number and the geometry it sizes. This route therefore states less than the DXF
route, and says so on every characteristic:

- every callout is PARSED from text and goes to NEEDS_REVIEW, never DETECTED;
- the geometry cross-check is NOT_VERIFIABLE (there is nothing independent to compare with);
- the kind comes only from the text (⌀ / R / °); a bare number is recorded as linear with a note;
- positions are page millimetres (the sheet, not model space), so balloons land on the sheet.

CAD exporters split a callout into several text runs — the nominal, then a stacked upper and
lower deviation in a smaller font, often rotated with a vertical dimension. Runs are joined in
each run's own (rotated) frame: same-size runs on one baseline are joined inline, and two smaller
runs immediately right of a nominal, one above and one below its centre line, are joined as
"nominal upper/lower". Anything else stays separate, so a stack that cannot be paired shows up
as an orphan tolerance in the uninterpreted list rather than being attached to a guessed nominal.

A PDF with no text layer (scanned) is refused: raster/OCR is a different route.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import iso286
from ..callouts import normalise, parse_callout
from ..gdt import parse_unicode_frame
from ..model import (
    CharKind,
    Characteristic,
    Check,
    CheckOutcome,
    ProvenanceState,
    ReviewStatus,
    SourceLocation,
    UninterpretedAnnotation,
)
from ..units import UnitError
from .dxf import DrawingBundle, EntityCensusRow, sha256_file

PT_TO_MM = 25.4 / 72.0
_NUMBER = re.compile(r"\d")
# A run that is only a deviation ("+0.2", "-0", "0") — never a callout on its own.
_DEVIATION_ONLY = re.compile(r"^[+-]\s*\d*\.?\d+$|^0$")
# Text that starts the way a callout does: a count ("2x"), a symbol, or a number.
_CALLOUT_START = re.compile(r"^(?:\d+\s*x\s*)?(?:⌀|R\s*\d|M\d|\d|\.\d)", re.I)


@dataclass
class TextRun:
    idx: int
    page: int
    text: str
    angle: float                       # degrees, counter-clockwise
    size: float                        # font size in mm (run height proxy)
    box: tuple[float, float, float, float]        # page mm, axis aligned
    local: tuple[float, float, float, float] = (0, 0, 0, 0)   # in the run's own frame
    parts: list[int] = field(default_factory=list)

    @property
    def centre(self) -> tuple[float, float]:
        return ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)


def _rot(x: float, y: float, deg: float) -> tuple[float, float]:
    a = math.radians(-deg)
    return (x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a))


def _local_box(box, deg: float):
    xs, ys = zip(*(_rot(x, y, deg) for x in (box[0], box[2]) for y in (box[1], box[3])))
    return (min(xs), min(ys), max(xs), max(ys))


def _read_runs(page, page_no: int, start: int):
    import pypdfium2.raw as raw
    tp = page.get_textpage()
    runs, paths, images = [], [], 0
    for o in page.get_objects(textpage=tp):
        if o.type == raw.FPDF_PAGEOBJ_TEXT:
            text = (o.extract() or "").replace("\x00", "").strip()
            if not text:
                continue
            a, b, *_ = o.get_matrix().get()
            angle = round(math.degrees(math.atan2(b, a)), 3) % 360
            l, bt, r, t = o.get_bounds()
            scale = math.hypot(a, b) or 1.0
            runs.append(TextRun(start + len(runs), page_no, text, angle,
                                o.get_font_size() * scale * PT_TO_MM,
                                (l * PT_TO_MM, bt * PT_TO_MM, r * PT_TO_MM, t * PT_TO_MM)))
        elif o.type == raw.FPDF_PAGEOBJ_PATH:
            l, bt, r, t = o.get_bounds()
            paths.append((l * PT_TO_MM, bt * PT_TO_MM, r * PT_TO_MM, t * PT_TO_MM))
        elif o.type == raw.FPDF_PAGEOBJ_IMAGE:
            images += 1
    return runs, paths, images


def _orthogonal(angle: float) -> bool:
    return min(angle % 90, 90 - angle % 90) < 0.5


def group_runs(runs: list[TextRun]) -> list[TextRun]:
    """Join the text runs one callout was split into. See the module docstring for the rules."""
    for r in runs:
        r.local = _local_box(r.box, r.angle) if _orthogonal(r.angle) else r.box
        r.parts = [r.idx]
    used: set[int] = set()
    out: list[TextRun] = []
    same_angle = lambda a, b: abs((a.angle - b.angle + 180) % 360 - 180) < 0.5  # noqa: E731
    for main in sorted(runs, key=lambda r: (-r.size, r.idx)):
        if main.idx in used or _DEVIATION_ONLY.match(main.text.replace(" ", "")):
            continue
        used.add(main.idx)
        text, box, local = main.text, list(main.box), list(main.local)
        h = local[3] - local[1]
        if _orthogonal(main.angle):
            # inline continuation: same size, same baseline, small gap to the right
            grew = True
            while grew:
                grew = False
                for r in runs:
                    if (r.idx in used or not same_angle(r, main) or abs(r.size - main.size) > 0.15 * main.size
                            or abs(r.local[1] - local[1]) > 0.25 * h
                            or not (local[2] - 0.2 * h <= r.local[0] <= local[2] + 0.8 * h)):
                        continue
                    used.add(r.idx)
                    text = f"{text} {r.text}"
                    local[2] = max(local[2], r.local[2])
                    box = [min(box[0], r.box[0]), min(box[1], r.box[1]),
                           max(box[2], r.box[2]), max(box[3], r.box[3])]
                    main.parts.append(r.idx)
                    grew = True
            # stacked deviations: two smaller runs right of the nominal, straddling its centre
            mid = (local[1] + local[3]) / 2
            sats = [r for r in runs if r.idx not in used and same_angle(r, main)
                    and r.size < 0.85 * main.size
                    and local[2] - 0.3 * h <= r.local[0] <= local[2] + 1.0 * h
                    and local[1] - 0.5 * h <= (r.local[1] + r.local[3]) / 2 <= local[3] + 0.5 * h]
            upper = [r for r in sats if (r.local[1] + r.local[3]) / 2 > mid]
            lower = [r for r in sats if (r.local[1] + r.local[3]) / 2 <= mid]
            if len(upper) == 1 and len(lower) == 1:
                up, lo = upper[0], lower[0]
                text = f"{text} {up.text.strip()}/{lo.text.strip()}"
                for r in (up, lo):
                    used.add(r.idx)
                    main.parts.append(r.idx)
                    box = [min(box[0], r.box[0]), min(box[1], r.box[1]),
                           max(box[2], r.box[2]), max(box[3], r.box[3])]
        out.append(TextRun(main.idx, main.page, re.sub(r"\s+", " ", text).strip(), main.angle,
                           main.size, tuple(box), tuple(local), list(main.parts)))
    for r in runs:                      # orphans (e.g. an unpaired deviation) stay visible
        if r.idx not in used:
            out.append(r)
    return sorted(out, key=lambda r: r.idx)


def _union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def read_pdf(path: str | Path, declared_unit: str | None = None,
             revision: str | None = None) -> DrawingBundle:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"drawing not found: {p}")
    if not declared_unit:
        raise UnitError("a PDF does not declare the unit its callouts are written in. Pass "
                        "--units explicitly; reading a number as millimetres without the drawing "
                        "saying so would be an invention.")
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(str(p))
    except Exception as ex:          # untrusted input
        raise ValueError(f"PDF could not be parsed: {type(ex).__name__}: {ex}") from ex
    doc_sha = sha256_file(p)
    unit = declared_unit
    warnings: list[str] = []
    if revision:
        revision, revision_source = revision.strip() or "A", "user-specified"
    else:
        revision, revision_source = "A", ("default - revision is not read from a PDF title "
                                          "block by this route; confirm before issuing")
        warnings.append("revision defaults to A: this route does not read PDF title blocks")

    all_runs: list[TextRun] = []
    paths: list[tuple] = []
    images = 0
    page_sizes = []
    for i in range(len(pdf)):
        page = pdf[i]
        w, h = page.get_size()
        page_sizes.append((w * PT_TO_MM, h * PT_TO_MM))
        runs, pth, img = _read_runs(page, i + 1, len(all_runs))
        all_runs += runs
        paths += pth if i == 0 else []
        images += img
    if not all_runs:
        raise ValueError("the PDF has no text layer" + (" (it contains images: a scanned "
                         "drawing needs OCR, which this route does not do)" if images else ""))
    if len(pdf) > 1:
        warnings.append(f"{len(pdf)} pages: callouts are read from every page, but positions and "
                        "the layout image refer to page 1 only")

    groups = group_runs(all_runs)
    characteristics: list[Characteristic] = []
    uninterpreted: list[UninterpretedAnnotation] = []
    notes: list[str] = []
    n = 0
    for g in groups:
        norm = normalise(g.text)
        cx, cy = g.centre
        handle = f"P{g.page}-T{g.idx:03d}" + ("+" + "+".join(str(x) for x in g.parts[1:])
                                               if len(g.parts) > 1 else "")
        if _DEVIATION_ONLY.match(norm.replace(" ", "")):
            uninterpreted.append(UninterpretedAnnotation(
                str(p), handle, "", g.text, "a tolerance run with no nominal beside it; it was not "
                "attached to a guessed dimension", g.page, cx, cy))
            continue
        if not _NUMBER.search(norm):
            continue
        frame = parse_unicode_frame(g.text)
        if frame is not None:
            if not frame.resolved:
                uninterpreted.append(UninterpretedAnnotation(
                    str(p), handle, "", g.text, "feature control frame not resolved: "
                    + (frame.unresolved_reason or ""), g.page, cx, cy))
                continue
            n += 1
            tol = frame.tolerance
            c = Characteristic(
                cid=f"CH-{n:03d}", kind=CharKind.GEOMETRIC, raw_callout=g.text, unit=unit,
                tolerance=tol, state=ProvenanceState.PARSED, review=ReviewStatus.NEEDS_REVIEW,
                source=SourceLocation(document=str(p), document_sha256=doc_sha, revision=revision,
                                      page=g.page, entity_handle=handle, entity_type="PDF_TEXT",
                                      layer="", anchor_x=cx, anchor_y=cy),
                limits=(0.0, tol.zone))
            c.record("extractor", "extracted", f"PDF text run(s) {handle} read as a frame")
            for note in frame.notes:
                c.record("parser", "noted", note)
            c.checks.append(Check("geometry_cross_check", CheckOutcome.NOT_VERIFIABLE,
                                  "callout matches drawn geometry", "-",
                                  "a feature control frame states a zone, not a size"))
            c.fail("pdf_text_only", "read from PDF text: confirm the frame was read whole "
                                    "(symbols drawn as graphics are not text)", "warning")
            if tol.modifier in ("MMC", "LMC"):
                c.fail("gdt_bonus_tolerance_not_applied",
                       f"{tol.symbol} at {tol.modifier}: bonus tolerance not computed", "warning")
            characteristics.append(c)
            continue
        parsed = parse_callout(g.text, unit)
        if not parsed.resolved or (parsed.nominal is None and parsed.tolerance.kind != "limit_pair"):
            if parsed.resolved or not _CALLOUT_START.match(norm):
                notes.append(norm)      # prose with a number in it (material, part no., date)
            else:
                uninterpreted.append(UninterpretedAnnotation(
                    str(p), handle, "", g.text, "text with numbers that does not parse as a "
                    "callout: " + (parsed.unresolved_reason or "not a recognised notation"),
                    g.page, cx, cy))
            continue
        n += 1
        kind = parsed.kind if parsed.kind is not CharKind.UNKNOWN else CharKind.LINEAR
        from ..limits import resolve_limits
        c = Characteristic(
            cid=f"CH-{n:03d}", kind=kind, raw_callout=g.text, unit=unit,
            nominal=parsed.nominal, tolerance=parsed.tolerance, count=parsed.count,
            state=ProvenanceState.PARSED, review=ReviewStatus.NEEDS_REVIEW,
            source=SourceLocation(document=str(p), document_sha256=doc_sha, revision=revision,
                                  page=g.page, entity_handle=handle, entity_type="PDF_TEXT",
                                  layer="", anchor_x=cx, anchor_y=cy),
            limits=resolve_limits(parsed.nominal, parsed.tolerance, unit))
        c.record("extractor", "extracted",
                 f"PDF text run(s) {handle} on page {g.page}, angle {g.angle:g} deg")
        for note in parsed.notes:
            c.record("parser", "noted", note)
        if parsed.kind is CharKind.UNKNOWN:
            c.record("parser", "noted", "no ⌀/R/° in the text: recorded as linear; a PDF carries "
                                        "no dimension type to confirm it")
        c.checks.append(Check("geometry_cross_check", CheckOutcome.NOT_VERIFIABLE,
                              "callout matches drawn geometry", "-",
                              "a PDF has no dimension entities or definition points, so the "
                              "written value has no second source to be checked against"))
        c.fail("pdf_text_only", "read from PDF text: not linked to a dimension entity and not "
                                "cross-checked against geometry; confirm it is a dimension of the "
                                "part (not a title-block or note number)", "warning")
        if c.limits is None and kind is not CharKind.ANGULAR:
            c.fail("no_limits", "no definite upper/lower limits could be derived from the stated "
                                "tolerance; inspection cannot pass/fail this characteristic",
                   "warning")
        characteristics.append(c)

    page_w, page_h = page_sizes[0]
    extents = (0.0, 0.0, page_w, page_h)
    # Paths covering most of the sheet are the border/title frame, not the part.
    small = [b for b in paths if (b[2] - b[0]) < 0.6 * page_w and (b[3] - b[1]) < 0.6 * page_h]
    content_extents = _union(small) if small else extents
    used = sum(len(c.source.entity_handle.split("+")) for c in characteristics)
    census = [EntityCensusRow("PDF_TEXT", len(all_runs), used,
                              "text runs; joined into callouts where split by the exporter"),
              EntityCensusRow("PDF_PATH", len(paths), 0, "page-1 vector paths; extents only")]
    if images:
        census.append(EntityCensusRow("PDF_IMAGE", images, 0, "raster content is not read"))
        warnings.append(f"{images} image object(s) on the page are not read (no OCR)")
    feature_support = {
        "dimension.linear": "partial - from text only, kind not confirmable, no cross-check",
        "dimension.diameter": "partial - from the ⌀ symbol in text, no cross-check",
        "dimension.radius": "partial - from the R prefix in text, no cross-check",
        "dimension.angular": "partial - from the ° symbol in text, no cross-check",
        "tolerance.symmetric_deviations": "supported (inline or stacked runs)",
        "tolerance.limit_pair": "supported",
        "tolerance.iso_fit_class": iso286.support_statement(),
        "tolerance.thread_designation": "NOT supported - left unresolved",
        "gdt.feature_control_frame": "partial - frames written as Unicode text (⌖ ⊥ ∥ ...) are read; frames drawn as graphics are not text and are missed",
        "title_block": "NOT read by this route",
        "raster_ocr": "NOT supported - a scanned PDF is refused",
    }
    return DrawingBundle(
        path=str(p), sha256=doc_sha, revision=revision, revision_source=revision_source,
        unit=unit, extents=extents, content_extents=content_extents, notes=notes,
        page_count=len(pdf), characteristics=characteristics, uninterpreted=uninterpreted,
        census=census, warnings=warnings, feature_support=feature_support,
        dxf_version="", dimstyle_notes=[])
