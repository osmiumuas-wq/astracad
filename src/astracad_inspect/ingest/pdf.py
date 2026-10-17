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
    quad: tuple | None = None          # exact rotated outline, page mm (4 corners)

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
    runs, paths, images, segs = [], [], 0, []
    for o in page.get_objects(textpage=tp):
        if o.type == raw.FPDF_PAGEOBJ_TEXT:
            text = (o.extract() or "").replace("\x00", "").strip()
            if not text:
                continue
            a, b, *_ = o.get_matrix().get()
            angle = round(math.degrees(math.atan2(b, a)), 3) % 360
            l, bt, r, t = o.get_bounds()
            scale = math.hypot(a, b) or 1.0
            try:
                quad = tuple((x * PT_TO_MM, y * PT_TO_MM) for x, y in o.get_quad_points())
            except Exception:
                quad = None
            runs.append(TextRun(start + len(runs), page_no, text, angle,
                                o.get_font_size() * scale * PT_TO_MM,
                                (l * PT_TO_MM, bt * PT_TO_MM, r * PT_TO_MM, t * PT_TO_MM),
                                quad=quad))
        elif o.type == raw.FPDF_PAGEOBJ_PATH:
            l, bt, r, t = o.get_bounds()
            paths.append((l * PT_TO_MM, bt * PT_TO_MM, r * PT_TO_MM, t * PT_TO_MM))
            segs.extend(_path_segments(o))
        elif o.type == raw.FPDF_PAGEOBJ_IMAGE:
            images += 1
    return runs, paths, images, segs


def _path_segments(o) -> list[tuple[float, float, float, float]]:
    """Straight pieces of a vector path in page mm (curves are flattened to their end points;
    SOLIDWORKS already writes circles as many short lines)."""
    import ctypes

    import pypdfium2.raw as raw
    a, b, c, d, e, f = o.get_matrix().get()
    out, prev, ctrl, start = [], None, [], None
    x, y = ctypes.c_float(), ctypes.c_float()
    for i in range(raw.FPDFPath_CountSegments(o.raw)):
        sg = raw.FPDFPath_GetPathSegment(o.raw, i)
        raw.FPDFPathSegment_GetPoint(sg, x, y)
        px = (a * x.value + c * y.value + e) * PT_TO_MM
        py = (b * x.value + d * y.value + f) * PT_TO_MM
        kind = raw.FPDFPathSegment_GetType(sg)
        if kind == raw.FPDF_SEGMENT_BEZIERTO and prev is not None:
            ctrl.append((px, py))
            if len(ctrl) == 3:                 # cubic: p0=prev, c1, c2, p3
                (x1, y1), (x2, y2), (x3, y3) = ctrl
                last = prev
                for t in (0.25, 0.5, 0.75, 1.0):
                    u = 1 - t
                    qx = u**3 * prev[0] + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3
                    qy = u**3 * prev[1] + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3
                    out.append((last[0], last[1], qx, qy))
                    last = (qx, qy)
                prev, ctrl = (x3, y3), []
                if raw.FPDFPathSegment_GetClose(sg) and start is not None and prev != start:
                    out.append((prev[0], prev[1], start[0], start[1]))
                    prev = start
            continue
        ctrl = []
        if kind == raw.FPDF_SEGMENT_MOVETO:
            start = (px, py)
        elif prev is not None:
            out.append((prev[0], prev[1], px, py))
        prev = (px, py)
        # a closing flag means an edge back to the subpath start (rectangles are drawn this way)
        if raw.FPDFPathSegment_GetClose(sg) and start is not None and prev != start:
            out.append((prev[0], prev[1], start[0], start[1]))
            prev = start
    return out


_BARE_INT = re.compile(r"^\d{1,3}$")


class SheetGeometry:
    """The page's vector lines, used to tell a dimension callout from the other numbers a CAD sheet
    carries: zone labels on the border, balloon item numbers inside circles, and table cells.
    Measured on SOLIDWORKS PDFs (2026-10-17): those three were 290 of 302 false rows."""

    def __init__(self, segs, page_w: float, page_h: float, centres=()):
        import numpy as np
        self.centres = list(centres)          # centres of every text group on the page
        self.texts: list = []                 # (cx, cy, text) of every text group on the page
        self.w, self.h = page_w, page_h
        a = np.asarray(segs or [(0, 0, 0, 0)], dtype=float)
        self.seg = a
        dx, dy = a[:, 2] - a[:, 0], a[:, 3] - a[:, 1]
        self.horiz = a[np.abs(dy) < 0.05]
        self.vert = a[np.abs(dx) < 0.05]
        self.pts = np.vstack([a[:, :2], a[:, 2:]])

    def is_border_label(self, text: str, cx: float, cy: float) -> bool:
        edge = min(cx, cy, self.w - cx, self.h - cy)
        return edge < 15.0 and (_BARE_INT.match(text) is not None or re.fullmatch(r"[A-Z]", text) is not None)

    def is_balloon(self, text: str, cx: float, cy: float, th: float) -> bool:
        """A bare item number inside a drawn circle that hugs it.

        Candidate points at each radius band around the number are fitted with a circle; it is a
        balloon only if the fit is tight (a drawn circle, not nearby dimension lines that happen to
        surround the text), its centre is on the number, it covers >= 300 deg, its radius suits the
        text, and nothing is drawn inside it. Each band is judged on its own, so a neighbouring
        balloon or a leader cannot drag the estimate off (SOLIDWORKS "leg": stacked item balloons
        were read as dimensions; "box_intro": dimension lines around "95" are not a ring)."""
        import numpy as np
        if not _BARE_INT.match(text):
            return False
        d = np.hypot(self.pts[:, 0] - cx, self.pts[:, 1] - cy)
        win = (d > 0.35 * th) & (d < 2.6 * th)
        if win.sum() < 8:
            return False
        r, p = d[win], self.pts[win]
        for rad in np.unique(np.round(r / (0.1 * th)) * (0.1 * th)):
            band = np.abs(r - rad) < 0.2 * rad
            if band.sum() < 8:
                continue
            q = p[band]
            # algebraic circle fit: x^2 + y^2 + D x + E y + F = 0
            A = np.column_stack([q[:, 0], q[:, 1], np.ones(len(q))])
            b = -(q[:, 0] ** 2 + q[:, 1] ** 2)
            try:
                (D, E, F), *_ = np.linalg.lstsq(A, b, rcond=None)
            except np.linalg.LinAlgError:
                continue
            fx, fy = -D / 2, -E / 2
            fr2 = fx * fx + fy * fy - F
            if fr2 <= 0:
                continue
            fr = float(np.sqrt(fr2))
            if not (0.4 * th <= fr <= 2.2 * th) or np.hypot(fx - cx, fy - cy) > 0.35 * fr:
                continue
            dq = np.hypot(q[:, 0] - fx, q[:, 1] - fy)
            on = np.abs(dq - fr) < 0.04 * fr
            if on.sum() < 8:
                continue
            ang = np.degrees(np.arctan2(q[on, 1] - fy, q[on, 0] - fx))
            if len(np.unique((ang // 30).astype(int))) < 10:          # >= 300 deg of coverage
                continue
            inner = np.hypot(self.pts[:, 0] - fx, self.pts[:, 1] - fy) < 0.75 * fr
            if inner.sum() <= 2:
                return True
        return False

    def diameter_glyph_left_of(self, box, th: float) -> bool:
        """SOLIDWORKS draws the ⌀ sign as vector graphics, not text: a small ring with a slash just
        left of the number. Looked for in a box one text-height wide left of the run."""
        import numpy as np
        x0, y0, x1, y1 = box
        cy = (y0 + y1) / 2
        lo_x, hi_x = x0 - 1.4 * th, x0 + 0.1 * th
        P = self.pts
        sel = (P[:, 0] >= lo_x) & (P[:, 0] <= hi_x) & (P[:, 1] >= cy - 0.75 * th) & (P[:, 1] <= cy + 0.75 * th)
        if sel.sum() < 6:
            return False
        q = P[sel]
        cxg, cyg = q[:, 0].mean(), q[:, 1].mean()
        d = np.hypot(q[:, 0] - cxg, q[:, 1] - cyg)
        med = np.median(d)
        if not (0.15 * th <= med <= 0.6 * th):
            return False
        ring = np.abs(d - med) < 0.25 * med
        ang = np.degrees(np.arctan2(q[ring, 1] - cyg, q[ring, 0] - cxg))
        if len(np.unique((ang // 45).astype(int))) < 6:
            return False
        S = self.seg
        sx = (np.minimum(S[:, 0], S[:, 2]) >= lo_x - 0.2 * th) & (np.maximum(S[:, 0], S[:, 2]) <= hi_x + 0.2 * th)
        sy = (np.minimum(S[:, 1], S[:, 3]) >= cy - 0.9 * th) & (np.maximum(S[:, 1], S[:, 3]) <= cy + 0.9 * th)
        L = S[sx & sy]
        ln = np.hypot(L[:, 2] - L[:, 0], L[:, 3] - L[:, 1])
        slope = np.degrees(np.arctan2(np.abs(L[:, 3] - L[:, 1]), np.abs(L[:, 2] - L[:, 0]) + 1e-9))
        return bool(((ln > 0.6 * med * 2) & (slope > 30) & (slope < 80)).any())

    def frame_at(self, box, th: float):
        """A feature control frame drawn as graphics around a number: a row of square-ish cells of
        one height, a stroke-only symbol cell left of the value. Returns (symbol or None, datums,
        evidence) or None when the number is not in such a frame."""
        import numpy as np
        x0, y0, x1, y1 = box
        cy = (y0 + y1) / 2
        H, V = self.horiz, self.vert
        hx0, hx1 = np.minimum(H[:, 0], H[:, 2]), np.maximum(H[:, 0], H[:, 2])
        top = H[(hx0 <= x0) & (hx1 >= x1) & (H[:, 1] > y1 - 0.1) & (H[:, 1] < cy + 2.1 * th)]
        bot = H[(hx0 <= x0) & (hx1 >= x1) & (H[:, 1] < y0 + 0.1) & (H[:, 1] > cy - 2.1 * th)]
        if not len(top) or not len(bot):
            return None
        yt, yb = top[:, 1].min(), bot[:, 1].max()
        fh = yt - yb
        if not (1.2 * th <= fh <= 4.0 * th):           # SOLIDWORKS 1.96, reportlab 3.02 measured
            return None
        vy0, vy1 = np.minimum(V[:, 1], V[:, 3]), np.maximum(V[:, 1], V[:, 3])
        # cell dividers of a frame start and end ON its top and bottom rules; part edges and
        # extension lines that merely pass through do not (measured: a leg dimension '1500' boxed
        # by part edges had been read as a frame)
        seps = V[(np.abs(vy0 - yb) < 0.3) & (np.abs(vy1 - yt) < 0.3)]
        xs = np.unique(np.round(seps[:, 0], 1))
        left = xs[xs <= x0 + 0.2]
        if len(left) < 2:
            return None
        cl1 = left.max()
        cl0 = left[left < cl1 - 0.3 * fh]
        if not len(cl0):
            return None
        cl0 = cl0.max()
        if not (0.6 * fh <= cl1 - cl0 <= 1.6 * fh):        # the symbol cell is about square
            return None
        # and the frame's top and bottom rules begin at its left edge
        def starts_at(rule_y):
            R = H[np.abs(H[:, 1] - rule_y) < 0.3]
            return bool(len(R)) and bool((np.abs(np.minimum(R[:, 0], R[:, 2]) - cl0) < 0.3).any())
        if not (starts_at(yt) and starts_at(yb)):
            return None
        if any(cl0 < tx < cl1 and yb < ty < yt for tx, ty, _ in self.texts):
            return None                                    # a text cell, not a symbol cell
        S = self.seg
        m = 0.08 * fh
        inside = S[(np.minimum(S[:, 0], S[:, 2]) > cl0 + m) & (np.maximum(S[:, 0], S[:, 2]) < cl1 - m)
                   & (np.minimum(S[:, 1], S[:, 3]) > yb + m) & (np.maximum(S[:, 1], S[:, 3]) < yt - m)]
        symbol, why = _classify_symbol(inside, ((cl0 + cl1) / 2, (yb + yt) / 2), fh)
        right = xs[xs >= x1 - 0.2]
        datums = []
        if len(right):
            fr_end = H[(np.abs(H[:, 1] - yt) < 0.3)]
            end = max(np.maximum(fr_end[:, 0], fr_end[:, 2]).max(), right.max())
            for tx, ty, tt in sorted(self.texts):
                if x1 < tx < end and yb < ty < yt and re.fullmatch(r"[A-Z](?:-[A-Z])?", tt.strip()):
                    datums.append(tt.strip())
        return symbol, datums, why

    def is_table_cell(self, box, th: float) -> bool:
        """Ruled on all four sides by lines that run past the text: a table cell, not a callout."""
        import numpy as np
        x0, y0, x1, y1 = box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        H, V = self.horiz, self.vert
        hx0, hx1 = np.minimum(H[:, 0], H[:, 2]), np.maximum(H[:, 0], H[:, 2])
        spans_x = (hx0 <= x0 + 0.2) & (hx1 >= x1 - 0.2)
        above = spans_x & (H[:, 1] >= y1 - 0.2) & (H[:, 1] <= y1 + 2.5 * th)
        below = spans_x & (H[:, 1] <= y0 + 0.2) & (H[:, 1] >= y0 - 2.5 * th)
        vy0, vy1 = np.minimum(V[:, 1], V[:, 3]), np.maximum(V[:, 1], V[:, 3])
        spans_y = (vy0 <= y0 + 0.2) & (vy1 >= y1 - 0.2)
        left = spans_y & (V[:, 0] <= x0 + 0.2) & (V[:, 0] >= x0 - 60)
        right = spans_y & (V[:, 0] >= x1 - 0.2) & (V[:, 0] <= x1 + 60)
        if not (above.any() and below.any() and left.any() and right.any()):
            return False
        # Dimension text between its extension lines, with the part edge above and the dimension
        # line below, is "ruled on four sides" too (measured on SOLIDWORKS sheets). What makes a
        # table is rows: another text between the SAME two rules, beside this one.
        ya, yb = H[above, 1].min(), H[below, 1].max()
        # Rules are often drawn per cell, so "the same rule" means a rule at the same height that
        # also spans the neighbouring text, not the same segment.
        at_a = np.abs(H[:, 1] - ya) < 0.3
        at_b = np.abs(H[:, 1] - yb) < 0.3
        for ox, oy in self.centres:
            if not (yb < oy < ya) or (x0 - 0.5 <= ox <= x1 + 0.5):
                continue
            if (at_a & (hx0 <= ox) & (hx1 >= ox)).any() and (at_b & (hx0 <= ox) & (hx1 >= ox)).any():
                return True
        return False


def _classify_symbol(segs, centre, size):
    """Name the ISO 1101 characteristic from the strokes inside a symbol cell, or None.
    Rules use stroke direction counts and rings (points at one radius all round)."""
    import numpy as np
    if not len(segs):
        return None, "empty symbol cell"
    dx, dy = segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1]
    ln = np.hypot(dx, dy)
    ang = np.degrees(np.arctan2(np.abs(dy), np.abs(dx) + 1e-12))
    long_ = ln > 0.25 * size
    nh = int((long_ & (ang < 10)).sum())
    nv = int((long_ & (ang > 80)).sum())
    ns = int((long_ & (ang >= 10) & (ang <= 80)).sum())
    pts = np.vstack([segs[:, :2], segs[:, 2:]])
    cx, cy = pts[:, 0].mean(), pts[:, 1].mean()
    d = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
    short = ln < 0.25 * size
    rings = []
    if short.sum() >= 8:
        sp = np.vstack([segs[short, :2], segs[short, 2:]])
        sd = np.hypot(sp[:, 0] - cx, sp[:, 1] - cy)
        for r in sorted(set(np.round(sd / (0.08 * size)).astype(int))):
            band = np.abs(sd - r * 0.08 * size) < 0.06 * size
            if band.sum() >= 8:
                a = np.degrees(np.arctan2(sp[band, 1] - cy, sp[band, 0] - cx))
                cov = len(np.unique((a // 30).astype(int)))
                if not rings or abs(r * 0.08 * size - rings[-1][0]) > 0.1 * size:
                    rings.append((r * 0.08 * size, cov))
    full = [r for r in rings if r[1] >= 9]
    arcs = [r for r in rings if 3 <= r[1] < 9]
    sig = f"h={nh} v={nv} slanted={ns} rings={len(full)} arcs={len(arcs)}"
    if len(full) >= 2 and not (nh or nv or ns):
        return "concentricity", sig
    if len(full) == 1:
        if nh == 1 and nv == 1:
            return "position", sig
        if ns == 2 and not (nh or nv):
            return "cylindricity", sig
        if not (nh or nv or ns):
            return "circularity", sig
        return None, sig
    if arcs and not full:
        return ("profile of a surface" if nh >= 1 else "profile of a line"), sig
    if nh == 1 and not (nv or ns):
        return "straightness", sig
    if nh == 3 and not (nv or ns):
        return "symmetry", sig
    if nh == 1 and nv == 1 and not ns:
        return "perpendicularity", sig
    if ns == 2 and not (nh or nv):
        return "parallelism", sig
    if nh == 2 and ns == 2 and not nv:
        return "flatness", sig
    if nh == 1 and ns == 1 and not nv:
        return "angularity", sig
    return None, sig


def _orthogonal(angle: float) -> bool:
    return min(angle % 90, 90 - angle % 90) < 0.5


def group_runs(runs: list[TextRun]) -> list[TextRun]:
    """Join the text runs one callout was split into. See the module docstring for the rules."""
    for r in runs:
        if r.quad:
            xs, ys = zip(*(_rot(x, y, r.angle) for x, y in r.quad))
            r.local = (min(xs), min(ys), max(xs), max(ys))
        else:
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
        if main.quad or _orthogonal(main.angle):
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
                    and r.size <= 1.05 * main.size
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
    sheet_geo: dict[int, SheetGeometry] = {}
    paths: list[tuple] = []
    images = 0
    page_sizes = []
    for i in range(len(pdf)):
        page = pdf[i]
        w, h = page.get_size()
        page_sizes.append((w * PT_TO_MM, h * PT_TO_MM))
        runs, pth, img, segs = _read_runs(page, i + 1, len(all_runs))
        sheet_geo[i + 1] = SheetGeometry(segs, w * PT_TO_MM, h * PT_TO_MM)
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
    for pg_no, geo_ in sheet_geo.items():
        geo_.centres = [gr.centre for gr in groups if gr.page == pg_no]
        geo_.texts = [(gr.centre[0], gr.centre[1], normalise(gr.text)) for gr in groups if gr.page == pg_no]
    from collections import Counter
    skipped: Counter = Counter()
    zone_labels: list = []
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
            # row labels of the sheet grid are letters: kept for zone references, never callouts
            geo_ = sheet_geo.get(g.page)
            if geo_ is not None and re.fullmatch(r"[A-Z]", norm.strip()) and                     geo_.is_border_label(norm.strip(), cx, cy):
                zone_labels.append((g.page, norm.strip(), cx, cy))
            continue
        geo = sheet_geo.get(g.page)
        # visual text height from the run's own outline: a font size can be well above the drawn
        # glyph height (measured 8.47 vs 6.3 mm on a SOLIDWORKS balloon), which shifts every
        # proportion used below
        lh = g.local[3] - g.local[1] if g.local and g.local[3] > g.local[1] else 0.0
        th = max(min(g.size, lh) if lh > 0 else g.size, 1.0)
        if geo is not None:
            if geo.is_border_label(norm, cx, cy):
                skipped["border zone label"] += 1
                zone_labels.append((g.page, norm, cx, cy))
                continue
            if geo.is_balloon(norm, cx, cy, th):
                skipped["balloon item number"] += 1
                continue
            if (_orthogonal(g.angle) and g.angle % 180 < 1 and geo.is_table_cell(g.box, th)
                    and geo.frame_at(g.box, th) is None):       # a frame with datum cells is not a table row
                skipped["table cell"] += 1
                notes.append(f"table cell (page {g.page}): {norm}")
                continue
        if geo is not None and g.angle % 360 < 1 and re.fullmatch(r"[⌀Øø]?\s*\d*\.?\d+", norm.strip()):
            fr = geo.frame_at(g.box, th)
            if fr is not None:
                symbol, datums, evidence = fr
                zone = float(re.sub(r"[^\d.]", "", norm))
                diam = bool(re.match(r"\s*[⌀Øø]", norm))
                if symbol is None:
                    uninterpreted.append(UninterpretedAnnotation(
                        str(p), handle, "", g.text, "number inside a feature control frame drawn as "
                        f"graphics; its symbol was not recognised from the strokes ({evidence}); "
                        f"zone {zone:g}, datums {datums or 'none'}", g.page, cx, cy))
                    continue
                from ..gdt import GeometricTolerance
                n += 1
                tol = GeometricTolerance(symbol, zone, diam, None, tuple(datums))
                c = Characteristic(
                    cid=f"CH-{n:03d}", kind=CharKind.GEOMETRIC, raw_callout=g.text, unit=unit,
                    tolerance=tol, state=ProvenanceState.PARSED, review=ReviewStatus.NEEDS_REVIEW,
                    source=SourceLocation(document=str(p), document_sha256=doc_sha, revision=revision,
                                          page=g.page, entity_handle=handle, entity_type="PDF_FRAME",
                                          layer="", anchor_x=cx, anchor_y=cy,
                                          text_box=tuple(float(v) for v in g.box)),
                    limits=(0.0, zone))
                c.record("extractor", "extracted",
                         f"zone {norm} inside a frame drawn as vector graphics; symbol {symbol} "
                         f"recognised from its strokes ({evidence}); datums {datums or 'none'}")
                c.checks.append(Check("geometry_cross_check", CheckOutcome.NOT_VERIFIABLE,
                                      "callout matches drawn geometry", "-",
                                      "a feature control frame states a zone, not a size"))
                c.fail("pdf_frame_from_graphics", "frame and symbol read from vector strokes, not "
                                                  "text: confirm the characteristic", "warning")
                characteristics.append(c)
                continue
        glyph_note = None
        if (geo is not None and g.angle % 360 < 1 and not re.match(r"^\s*(?:\d+\s*[xX]\s*)?[⌀Øø∅]", g.text)
                and re.match(r"^\s*\.?\d", g.text) and geo.diameter_glyph_left_of(g.box, th)):
            g = TextRun(g.idx, g.page, "⌀" + g.text, g.angle, g.size, g.box, g.local, g.parts, g.quad)
            glyph_note = ("diameter sign drawn as vector graphics left of the text (a ring with a "
                          "slash), not as a character; added from the sheet geometry")
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
                                      layer="", anchor_x=cx, anchor_y=cy,
                                      text_box=tuple(float(v) for v in g.box)),
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
                                  layer="", anchor_x=cx, anchor_y=cy,
                                  text_box=tuple(float(v) for v in g.box)),
            limits=resolve_limits(parsed.nominal, parsed.tolerance, unit))
        c.record("extractor", "extracted",
                 f"PDF text run(s) {handle} on page {g.page}, angle {g.angle:g} deg")
        if glyph_note:
            c.record("extractor", "noted", glyph_note)
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

    from ..zones import assign_zones
    assign_zones(characteristics, zone_labels)
    page_w, page_h = page_sizes[0]
    extents = (0.0, 0.0, page_w, page_h)
    # Paths covering most of the sheet are the border/title frame, not the part.
    small = [b for b in paths if (b[2] - b[0]) < 0.6 * page_w and (b[3] - b[1]) < 0.6 * page_h]
    content_extents = _union(small) if small else extents
    used = sum(len(c.source.entity_handle.split("+")) for c in characteristics)
    census = [EntityCensusRow("PDF_TEXT", len(all_runs), used,
                              "text runs; joined into callouts where split by the exporter"),
              EntityCensusRow("PDF_PATH", len(paths), 0, "page-1 vector paths; extents only")]
    for why, k in sorted(skipped.items()):
        # recorded, not hidden: text the sheet geometry shows is not a dimension callout
        census.append(EntityCensusRow(f"PDF_TEXT:{why}", k, 0, f"{k} run group(s) classified as {why} "
                                      "by the sheet's vector lines; not treated as callouts"))
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
        dxf_version="", dimstyle_notes=[],
        page_frames={i: (0.0, 0.0, w, h) for i, (w, h) in enumerate(page_sizes, start=1)})
