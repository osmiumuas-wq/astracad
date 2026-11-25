"""Drawing -> CAD: rebuild a part from its 2D drawing (DXF) as a real feature model.

Scope of this version, stated rather than guessed: a part that is ONE extrusion - a constant section
through a constant thickness, with openings that go all the way through - drawn as aligned
orthographic views (any projection: views are matched by alignment, not by where they sit).

- the section comes from the one view whose visible edges close into an outline and inner loops,
  copied exactly (lines, arcs, circles);
- the thickness comes from the aligned views, which must be rectangles of one common depth, and every
  line across them must run the full depth at a place the section explains (a hole's edge, a tangent);
- the scale of each view comes from its own dimensions (DXF measurement x DIMLFAC); a dimension that
  contradicts the geometry it measures stops the rebuild;
- an opening counts as "through" only when hidden lines show it through the whole depth in the aligned
  views - or when the user says so for a drawing without hidden lines.

Anything else (steps, bosses, blind features, turned parts, splines, unaligned views, no dimensions) is
refused with the reason. The result is the same Interpretation the text requests produce, so it runs
through the same permission gate and the same kernel + STEP verification.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .requests import Fact, Interpretation

_HIDDEN = ("HIDDEN", "DASH", "DOT")
_CENTER = ("CENTER", "CENTRE", "DASHDOT", "PHANTOM", "CHAIN")
_INCH_UNITS = {1}
_MM_UNITS = {4}


@dataclass
class Edge:
    kind: str                       # "L" line, "A" arc (counter-clockwise from a0 to a1, radians), "C" circle
    style: str                      # visible | hidden | center
    v: tuple                        # L: x1, y1, x2, y2   A: cx, cy, r, a0, a1   C: cx, cy, r
    handle: str = ""

    @property
    def ends(self) -> tuple[tuple[float, float], tuple[float, float]] | None:
        if self.kind == "L":
            return (self.v[0], self.v[1]), (self.v[2], self.v[3])
        if self.kind == "A":
            cx, cy, r, a0, a1 = self.v
            return (cx + r * math.cos(a0), cy + r * math.sin(a0)), (cx + r * math.cos(a1), cy + r * math.sin(a1))
        return None

    def bbox(self) -> tuple[float, float, float, float]:
        if self.kind == "L":
            x1, y1, x2, y2 = self.v
            return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
        cx, cy, r = self.v[:3]
        if self.kind == "C":
            return cx - r, cy - r, cx + r, cy + r
        a0, a1 = self.v[3], self.v[4]
        (sx, sy), (ex, ey) = self.ends
        xs, ys = [sx, ex], [sy, ey]
        span = (a1 - a0) % (2 * math.pi)
        for k in range(4):                                  # quadrant points the arc passes
            t = k * math.pi / 2
            if (t - a0) % (2 * math.pi) <= span + 1e-12:
                xs.append(cx + r * math.cos(t))
                ys.append(cy + r * math.sin(t))
        return min(xs), min(ys), max(xs), max(ys)


@dataclass
class View:
    edges: list[Edge]
    bbox: tuple[float, float, float, float]
    dims: list[dict] = field(default_factory=list)
    k: float | None = None                                  # true length per sheet unit
    loops: list[list[tuple[Edge, bool]]] = field(default_factory=list)   # (edge, reversed)
    open_edges: list[Edge] = field(default_factory=list)

    @property
    def w(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def h(self) -> float:
        return self.bbox[3] - self.bbox[1]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _style(e, doc) -> str:
    lt = (e.dxf.get("linetype", "BYLAYER") or "BYLAYER").upper()
    if lt == "BYLAYER":
        try:
            lt = (doc.layers.get(e.dxf.layer).dxf.linetype or "CONTINUOUS").upper()
        except Exception:                                    # noqa: BLE001 - unknown layer: continuous
            lt = "CONTINUOUS"
    layer = (e.dxf.get("layer", "") or "").upper()
    if any(c in lt for c in _CENTER) or any(c in layer for c in _CENTER):
        return "center"
    if any(c in lt for c in _HIDDEN) or any(c in layer for c in _HIDDEN):
        return "hidden"
    return "visible"


def _read_edges(doc, problems: list[str]) -> list[Edge]:
    out: list[Edge] = []
    for e in doc.modelspace():
        t = e.dxftype()
        if e.dxf.get("layer", "").upper() == "DEFPOINTS":
            continue
        parts = list(e.virtual_entities()) if t in ("LWPOLYLINE", "POLYLINE") else [e]
        for p in parts:
            pt = p.dxftype()
            style = _style(e, doc)
            if pt == "LINE":
                s, en = p.dxf.start, p.dxf.end
                if math.hypot(en.x - s.x, en.y - s.y) > 1e-12:
                    out.append(Edge("L", style, (s.x, s.y, en.x, en.y), e.dxf.handle))
            elif pt == "ARC":
                c = p.dxf.center
                out.append(Edge("A", style, (c.x, c.y, p.dxf.radius, math.radians(p.dxf.start_angle),
                                             math.radians(p.dxf.end_angle)), e.dxf.handle))
            elif pt == "CIRCLE":
                c = p.dxf.center
                out.append(Edge("C", style, (c.x, c.y, p.dxf.radius), e.dxf.handle))
            elif pt in ("SPLINE", "ELLIPSE"):
                problems.append(f"{pt.lower()} edge (handle {e.dxf.handle}) - only lines, arcs and circles are rebuilt")
    return out


def _union(boxes: list[tuple[float, float, float, float]], gap: float) -> list[int]:
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            if a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]:
                parent[find(i)] = find(j)
    return [find(i) for i in range(len(boxes))]


_SHEETS_MM = [(1189, 841), (841, 594), (594, 420), (420, 297), (297, 210), (210, 148),       # ISO A0-A5
               (279.4, 215.9), (431.8, 279.4), (558.8, 431.8), (863.6, 558.8), (1117.6, 863.6)]  # ANSI A-E


def _views(edges: list[Edge], facts: list[Fact], dim_points: list[tuple[float, float]] = (),
           to_mm: float = 1.0) -> list[View]:
    if not edges:
        return []
    boxes = [e.bbox() for e in edges]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    diag = math.hypot(x1 - x0, y1 - y0)
    # A sheet frame encloses everything; its lines span (nearly) the whole sheet. They and any line
    # standing on them (a title block) are not part of a view. But a single view's own outline spans the
    # whole drawing too (found 2026-10-18: a gasket's outline was dropped and only its hole rebuilt), so
    # lines count as a frame only when no dimension measures them AND a title block stands on them or
    # the frame is a standard sheet size.
    frame = [i for i, (e, b) in enumerate(zip(edges, boxes)) if e.kind == "L" and e.style == "visible" and
             ((b[2] - b[0]) > 0.9 * (x1 - x0) or (b[3] - b[1]) > 0.9 * (y1 - y0))]
    tol = 1e-6 * diag
    if frame and any(_on_line(p, edges[i], max(tol, 1e-3)) for i in frame for p in dim_points):
        frame = []
    if frame:
        attached = sum(1 for i, e in enumerate(edges) if i not in frame and e.kind == "L"
                       and any(_on_line(p, edges[j], tol) for j in frame for p in e.ends))
        w_mm, h_mm = sorted(((x1 - x0) * to_mm, (y1 - y0) * to_mm), reverse=True)
        sheet = any(abs(w_mm - a) <= 0.03 * a and abs(h_mm - b) <= 0.03 * b for a, b in _SHEETS_MM)
        if attached < 3 and not sheet:
            frame = []
    dropped = set(frame)
    if frame:
        grew = True
        while grew:
            grew = False
            for i, e in enumerate(edges):
                if i in dropped or e.kind != "L":
                    continue
                for p in e.ends:
                    if any(_on_line(p, edges[j], 1e-6 * diag) for j in dropped):
                        dropped.add(i)
                        grew = True
                        break
        facts.append(Fact("sheet_frame", f"{len(dropped)} frame / title-block line(s) set aside", None,
                          "CALCULATED", "lines spanning the sheet, and lines standing on them"))
    keep = [i for i in range(len(edges)) if i not in dropped]
    roots = _union([boxes[i] for i in keep], gap=0.004 * diag)
    groups: dict[int, list[int]] = {}
    for i, r in zip(keep, roots):
        groups.setdefault(r, []).append(i)
    # a hole drawn in the middle of an outline touches none of its edges: groups whose extents overlap
    # are one view (repeated until nothing merges)
    merged = True
    while merged:
        merged = False
        gl = list(groups.values())
        ext = [(min(boxes[i][0] for i in g), min(boxes[i][1] for i in g), max(boxes[i][2] for i in g),
                max(boxes[i][3] for i in g)) for g in gl]
        r2 = _union(ext, gap=0.004 * diag)
        parent = list(range(len(gl)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(gl)):
            parent[find(i)] = find(r2[i])
        for i, a in enumerate(ext):
            for j in range(i + 1, len(ext)):
                b = ext[j]
                c_edges = [edges[k] for k in gl[i] + gl[j] if edges[k].style == "center"]
                if c_edges:
                    x_ov = min(a[2], b[2]) - max(a[0], b[0])
                    y_ov = min(a[3], b[3]) - max(a[1], b[1])
                    if (x_ov > 0.8 * min(a[2] - a[0], b[2] - b[0]) and abs(a[1] - b[3]) < 0.3 * diag) or \
                            (y_ov > 0.8 * min(a[3] - a[1], b[3] - b[1]) and abs(a[0] - b[2]) < 0.3 * diag):
                        parent[find(i)] = find(j)
        r3 = [find(i) for i in range(len(gl))]
        if len(set(r3)) < len(gl):
            merged = True
            groups = {}
            for g, r in zip(gl, r3):
                groups.setdefault(r, []).extend(g)
    views = []
    for idx in groups.values():
        es = [edges[i] for i in idx]
        if all(e.style == "center" for e in es):
            continue
        bb = [boxes[i] for i in idx if edges[i].style != "center"]
        views.append(View(es, (min(b[0] for b in bb), min(b[1] for b in bb), max(b[2] for b in bb), max(b[3] for b in bb))))
    return sorted(views, key=lambda v: -(v.w * v.h))


def _on_line(p, e: Edge, tol: float) -> bool:
    x1, y1, x2, y2 = e.v
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return False
    t = ((p[0] - x1) * dx + (p[1] - y1) * dy) / L2
    if t < -1e-9 or t > 1 + 1e-9:
        return False
    return math.hypot(x1 + t * dx - p[0], y1 + t * dy - p[1]) <= tol


def _loops(view: View, tol: float) -> None:
    """Simple closed cycles of the visible edges; everything else is an open edge."""
    # A face and the floor behind it project onto the same lines: an exact duplicate is one edge.
    vis, seen = [], set()
    for e in view.edges:
        if e.style != "visible":
            continue
        v = e.v if e.kind != "L" or (e.v[0], e.v[1]) <= (e.v[2], e.v[3]) else (e.v[2], e.v[3], e.v[0], e.v[1])
        key = (e.kind,) + tuple(round(c / tol) for c in v)
        if key not in seen:
            seen.add(key)
            vis.append(e)
    view.loops, view.open_edges = [], []
    for e in vis:
        if e.kind == "C":
            view.loops.append([(e, False)])
    chain = [e for e in vis if e.kind != "C"]
    nodes: list[tuple[float, float]] = []

    def node(p):
        for i, q in enumerate(nodes):
            if abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol:
                return i
        nodes.append(p)
        return len(nodes) - 1
    ends = [(node(e.ends[0]), node(e.ends[1])) for e in chain]
    adj: dict[int, list[int]] = {}
    for k, (a, b) in enumerate(ends):
        adj.setdefault(a, []).append(k)
        adj.setdefault(b, []).append(k)
    used: set[int] = set()
    for start in range(len(chain)):
        if start in used:
            continue
        comp, stack, seen_nodes = [], [start], set()
        while stack:                                      # the connected component of this edge
            k = stack.pop()
            if k in comp:
                continue
            comp.append(k)
            for nd in ends[k]:
                seen_nodes.add(nd)
                stack.extend(j for j in adj[nd] if j not in comp)
        used.update(comp)
        if all(len(adj[n]) == 2 for n in seen_nodes) and len(comp) >= 2:
            loop, k, at = [], comp[0], ends[comp[0]][0]
            for _ in range(len(comp)):
                a, b = ends[k]
                rev = a != at
                loop.append((chain[k], rev))
                at = a if rev else b
                k = next((j for j in adj[at] if j != k), None)
            view.loops.append(loop)
        else:
            view.open_edges.extend(chain[k] for k in comp)


def _seg_points(e: Edge, rev: bool, n: int = 24) -> list[tuple[float, float]]:
    if e.kind == "L":
        pts = [(e.v[0], e.v[1]), (e.v[2], e.v[3])]
    elif e.kind == "A":
        cx, cy, r, a0, a1 = e.v
        span = (a1 - a0) % (2 * math.pi) or 2 * math.pi
        pts = [(cx + r * math.cos(a0 + span * i / n), cy + r * math.sin(a0 + span * i / n)) for i in range(n + 1)]
    else:
        cx, cy, r = e.v
        pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1)]
    return pts[::-1] if rev else pts


def _polygon(loop) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for e, rev in loop:
        pts.extend(_seg_points(e, rev)[:-1] if e.kind != "C" else _seg_points(e, rev))
    return pts


def _area(loop) -> float:
    """Signed area, exact for arcs (Green's theorem per segment)."""
    a = 0.0
    for e, rev in loop:
        if e.kind == "L":
            x1, y1, x2, y2 = e.v if not rev else (e.v[2], e.v[3], e.v[0], e.v[1])
            a += (x1 * y2 - x2 * y1) / 2
        elif e.kind == "C":
            a += math.pi * e.v[2] ** 2
        else:
            cx, cy, r, a0, a1 = e.v
            d = (a1 - a0) % (2 * math.pi) or 2 * math.pi
            t0, t1 = (a0, a0 + d) if not rev else (a0 + d, a0)
            dd = t1 - t0
            a += (r * r * dd + r * (cx * (math.sin(t1) - math.sin(t0)) - cy * (math.cos(t1) - math.cos(t0)))) / 2
    return a


def _inside(pt, poly) -> bool:
    x, y, c = pt[0], pt[1], False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def _loop_bbox(loop) -> tuple[float, float, float, float]:
    bs = [e.bbox() for e, _ in loop]
    return min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)


def _dims(doc, problems: list[str]) -> list[dict]:
    out = []
    for e in doc.modelspace().query("DIMENSION"):
        try:
            meas = float(e.get_measurement())
        except Exception:                                    # noqa: BLE001 - angular etc.
            continue
        if (e.dxf.get("dimtype", 0) & 15) in (2, 5) or meas <= 0:      # angular: not a length
            continue
        try:
            lfac = float(e.override().get("dimlfac", 1.0) or 1.0)
        except Exception:                                    # noqa: BLE001
            lfac = 1.0
        text = (e.dxf.get("text", "") or "").strip()
        shown = meas * lfac
        if text not in ("", "<>"):
            import re
            m = re.fullmatch(r"[^\d.]*?(\d+(?:\.\d+)?)", text.replace("<>", ""))
            if m and "<>" not in text:
                written = float(m.group(1))
                if abs(written - shown) > 1e-3 * max(1.0, written):
                    problems.append(f"dimension {e.dxf.handle} says {written:g} but its geometry measures "
                                    f"{shown:g}: the drawing is not to scale there")
                shown = written
        pts = [e.dxf.get(a) for a in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5")]
        pts = [(p.x, p.y) for p in pts if p is not None]
        out.append({"handle": e.dxf.handle, "k": shown / meas, "value": shown, "pts": pts,
                    "text": text, "dimtype": e.dxf.get("dimtype", 0) & 15})
    return out


def _rectangle(view: View, tol: float) -> tuple[list[Edge] | None, list[Edge]]:
    """(the visible edges that make up the view's bounding rectangle, every other edge) - or (None, ...)
    when the visible edges along the four sides do not cover them completely."""
    x0, y0, x1, y1 = view.bbox
    on, rest = [], []
    for e in view.edges:
        if e.style == "center":
            continue
        if e.kind == "L" and e.style == "visible":
            a, b, c, d = e.v
            if (abs(a - c) <= tol and (abs(a - x0) <= tol or abs(a - x1) <= tol)) or \
                    (abs(b - d) <= tol and (abs(b - y0) <= tol or abs(b - y1) <= tol)):
                on.append(e)
                continue
        rest.append(e)

    def covered(vertical: bool, at: float, lo: float, hi: float) -> bool:
        spans = []
        for e in on:
            a, b, c, d = e.v
            if vertical and abs(a - at) <= tol and abs(c - at) <= tol:
                spans.append((min(b, d), max(b, d)))
            elif not vertical and abs(b - at) <= tol and abs(d - at) <= tol:
                spans.append((min(a, c), max(a, c)))
        reach = lo
        for s0, s1 in sorted(spans):
            if s0 > reach + tol:
                return False
            reach = max(reach, s1)
        return reach >= hi - tol
    ok = covered(True, x0, y0, y1) and covered(True, x1, y0, y1) and covered(False, y0, x0, x1) and covered(False, y1, x0, x1)
    return (on if ok else None), rest


def _span(view: View, axis: str, at: float, tol: float) -> list[Edge]:
    """Lines across a neighbour view at a coordinate, along the depth direction."""
    out = []
    for e in view.edges:
        if e.kind != "L" or e.style == "center":
            continue
        x1, y1, x2, y2 = e.v
        if axis == "x" and abs(x1 - at) <= tol and abs(x2 - at) <= tol:
            out.append(e)
        if axis == "y" and abs(y1 - at) <= tol and abs(y2 - at) <= tol:
            out.append(e)
    return out


def _texts(doc) -> list[str]:
    out = []
    for e in doc.modelspace():
        t = e.dxftype()
        if t in ("TEXT", "MTEXT"):
            out.append(e.plain_text() if t == "MTEXT" else e.dxf.get("text", ""))
        elif t == "INSERT":
            out += [a.dxf.get("text", "") for a in e.attribs]
            blk = doc.blocks.get(e.dxf.name) if not e.dxf.name.startswith("*") else None
            for b in blk or []:
                if b.dxftype() in ("TEXT", "MTEXT"):
                    out.append(b.plain_text() if b.dxftype() == "MTEXT" else b.dxf.get("text", ""))
    return [t for t in out if t]


def _stated(texts: list[str]) -> tuple[set, set]:
    """What the drawing itself writes: its sheet scale ("SCALE: 1:2") and a thickness note ("THK 3")."""
    import re
    scales, thick = set(), set()
    for t in texts:
        for m in re.finditer(r"\bSCALE\s*[:=]?\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", t, re.I):
            a, b = float(m.group(1)), float(m.group(2))
            if a > 0 and b > 0:
                scales.add((m.group(0).strip(), b / a))
        for m in re.finditer(r"\b(?:THK|THICKNESS|THICK)\b\.?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(MM|IN|\")?", t, re.I):
            thick.add((m.group(0).strip(), float(m.group(1)), (m.group(2) or "").upper()))
    return scales, thick


def _poly_area(pts: list[list[float]]) -> float:
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2.0


def _poly_cy(pts: list[list[float]]) -> float:
    a = _poly_area(pts)
    if abs(a) < 1e-12:
        return 0.0
    return sum((pts[i][1] + pts[(i + 1) % len(pts)][1]) * (pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]) for i in range(len(pts))) / (6.0 * a)


def _reconstruct_turned(views: list[View], doc, it: Interpretation, dims: list[dict], to_mm: float,
                        scale: float | None, through: bool, given_depth, path: Path,
                        reasons: list[str]) -> Interpretation | None:
    tol = 1e-5 * max(max(v.w, v.h) for v in views)
    solutions = []

    all_texts = _texts(doc)
    text_blob = " ".join(all_texts).upper()
    has_text_clue = bool(re.search(r"\b(SHAFT|ROD|PIN|SPINDLE|AXLE|BUSH(?:ING)?|SLEEVE|TUBE|CYLINDER)\b", text_blob)) or \
        any(c in text_blob for c in ("Ø", "%%C", "\\U+2205", "DIA"))

    for V in views:
        vis_lines = [e for e in V.edges if e.style == "visible" and e.kind == "L"]
        if len(vis_lines) < 4:
            continue
        if any(e.kind != "L" for e in V.edges if e.style == "visible"):
            continue

        candidate_axes = []
        center_lines = [e for e in V.edges if e.style == "center" and e.kind == "L"]
        for c in center_lines:
            x1, y1, x2, y2 = c.v
            if abs(y1 - y2) <= tol * 5:
                candidate_axes.append(("H", (y1 + y2) / 2.0, "centerline"))
            if abs(x1 - x2) <= tol * 5:
                candidate_axes.append(("V", (x1 + x2) / 2.0, "centerline"))

        if not candidate_axes:
            if V.w >= V.h:
                candidate_axes.append(("H", (V.bbox[1] + V.bbox[3]) / 2.0, "symmetry"))
            if V.h >= V.w:
                candidate_axes.append(("V", (V.bbox[0] + V.bbox[2]) / 2.0, "symmetry"))

        for orient, axis_val, axis_src in candidate_axes:
            if orient == "H":
                u_fn = lambda x, y: x
                v_fn = lambda x, y: y - axis_val
            else:
                u_fn = lambda x, y: y
                v_fn = lambda x, y: x - axis_val

            segs = []
            for e in vis_lines:
                x1, y1, x2, y2 = e.v
                segs.append(((u_fn(x1, y1), v_fn(x1, y1)), (u_fn(x2, y2), v_fn(x2, y2))))

            u_min = min(min(s[0][0], s[1][0]) for s in segs)
            u_max = max(max(s[0][0], s[1][0]) for s in segs)
            total_len = u_max - u_min
            if total_len <= tol:
                continue

            norm_segs = []
            for (u1, v1), (u2, v2) in segs:
                norm_segs.append(((u1 - u_min, v1), (u2 - u_min, v2)))

            # Check symmetry across v = 0
            symmetric = True
            for (u1, v1), (u2, v2) in norm_segs:
                if v1 * v2 < -tol * tol:
                    if abs(u1 - u2) > tol * 5 or abs(v1 + v2) > tol * 5:
                        symmetric = False
                        break
                else:
                    matched = False
                    for (ru1, rv1), (ru2, rv2) in norm_segs:
                        if (abs(u1 - ru1) <= tol * 5 and abs(v1 + rv1) <= tol * 5 and
                            abs(u2 - ru2) <= tol * 5 and abs(v2 + rv2) <= tol * 5) or \
                           (abs(u1 - ru2) <= tol * 5 and abs(v1 + rv2) <= tol * 5 and
                            abs(u2 - ru1) <= tol * 5 and abs(v2 + rv1) <= tol * 5):
                            matched = True
                            break
                    if not matched:
                        symmetric = False
                        break

            if not symmetric:
                reasons.append("the view is not symmetric across its centreline: not a revolved part")
                continue

            # Check for bore (hidden lines)
            hidden_lines = [e for e in V.edges if e.style == "hidden" and e.kind == "L"]
            r_inner = 0.0
            if hidden_lines:
                h_norm = []
                for e in hidden_lines:
                    x1, y1, x2, y2 = e.v
                    h_norm.append(((u_fn(x1, y1) - u_min, v_fn(x1, y1)), (u_fn(x2, y2) - u_min, v_fn(x2, y2))))
                par = [s for s in h_norm if abs(s[0][1] - s[1][1]) <= tol * 5 and abs(s[0][1]) > tol]
                if par:
                    pos_r = [s for s in par if s[0][1] > tol]
                    neg_r = [s for s in par if s[0][1] < -tol]
                    if pos_r and neg_r:
                        r_cand = pos_r[0][0][1]
                        spans = [s for s in pos_r if abs(s[0][1] - r_cand) <= tol * 5]
                        lo = min(min(s[0][0], s[1][0]) for s in spans)
                        hi = max(max(s[0][0], s[1][0]) for s in spans)
                        if abs(lo - 0) <= tol * 5 and abs(hi - total_len) <= tol * 5:
                            r_inner = r_cand
                        else:
                            reasons.append("hidden lines do not run the full length: blind bores are not rebuilt yet")
                            continue

            # Identify the outer step radii from the upper profile segments
            step_radii = {round(max(v1, v2), 4) for (u1, v1), (u2, v2) in norm_segs if max(v1, v2) > tol}

            # Check other views
            other_views = [o for o in views if o is not V]
            end_view = None
            if other_views:
                all_explained = True
                for o in other_views:
                    o_vis = [e for e in o.edges if e.style == "visible"]
                    if o_vis and all(e.kind == "C" for e in o_vis):
                        centers = {(round(e.v[0], 4), round(e.v[1], 4)) for e in o_vis}
                        if len(centers) == 1:
                            ocx, ocy = next(iter(centers))
                            if orient == "H" and abs(ocy - axis_val) <= tol * 10:
                                end_view = o
                                o_radii = {round(e.v[2], 4) for e in o_vis}
                                min_step_r = min(step_radii) if step_radii else 0.0
                                bore_circles = [r for r in o_radii if r < min_step_r - tol]
                                if bore_circles and r_inner == 0.0:
                                    if not through and len(hidden_lines) == 0:
                                        it.ambiguities.append("the drawing shows no hidden lines, so whether its inner bore goes through is not shown - confirm 'through' to rebuild")
                                        return it
                                    r_inner = min(bore_circles)
                                continue
                            elif orient == "V" and abs(ocx - axis_val) <= tol * 10:
                                end_view = o
                                continue
                    all_explained = False
                    break
                if not all_explained:
                    reasons.append(f"{len(other_views)} view(s) on the sheet are not explained by the turned part")
                    continue

            # If no other views, must be proven by diameter dimensions or text
            if not end_view:
                has_dia_dim = False
                for d in V.dims:
                    txt = d.get("text", "") or ""
                    dt = d.get("dimtype", 0)
                    if dt == 3 or any(c in txt for c in ("Ø", "%%c", "\\U+2205", "DIA", "dia")):
                        has_dia_dim = True
                        break
                if not (has_dia_dim or has_text_clue):
                    reasons.append("the drawing does not state whether this view is round (no diameter symbol 'Ø', no circular view, no round part note) - not assumed to be a shaft")
                    continue

            # Build half-profile in v >= r_inner
            upper_segs = []
            for (u1, v1), (u2, v2) in norm_segs:
                if v1 * v2 < -tol * tol:
                    v_top = max(v1, v2)
                    upper_segs.append(((u1, r_inner), (u1, v_top)))
                elif min(v1, v2) >= -tol:
                    upper_segs.append(((u1, max(v1, r_inner)), (u2, max(v2, r_inner))))

            upper_segs.append(((total_len, r_inner), (0.0, r_inner)))

            nodes = []

            def get_node(p):
                for i, q in enumerate(nodes):
                    if abs(p[0] - q[0]) <= tol * 5 and abs(p[1] - q[1]) <= tol * 5:
                        return i
                nodes.append(p)
                return len(nodes) - 1

            adj = {}
            edge_list = []
            for p1, p2 in upper_segs:
                if math.hypot(p1[0] - p2[0], p1[1] - p2[1]) > tol:
                    n1 = get_node(p1)
                    n2 = get_node(p2)
                    if n1 != n2:
                        k = len(edge_list)
                        edge_list.append((n1, n2))
                        adj.setdefault(n1, []).append((n2, k))
                        adj.setdefault(n2, []).append((n1, k))

            start_cand = [i for i, n in enumerate(nodes) if abs(n[0] - 0) <= tol * 5 and abs(n[1] - r_inner) <= tol * 5]
            if not start_cand:
                reasons.append("could not form a closed half-profile at origin")
                continue

            curr = start_cand[0]
            path_nodes = [curr]
            used_edges = set()
            while True:
                nxt = None
                for n, k in adj.get(curr, []):
                    if k not in used_edges:
                        used_edges.add(k)
                        nxt = n
                        break
                if nxt is None or nxt == path_nodes[0]:
                    break
                path_nodes.append(nxt)
                curr = nxt

            poly = [nodes[i] for i in path_nodes]
            if len(poly) < 3 or abs(_poly_area(poly)) <= tol:
                reasons.append("half-profile encloses no area")
                continue

            solutions.append((V, orient, poly, total_len, r_inner, end_view))

    if not solutions:
        return None

    V, orient, poly, total_len, r_inner, end_view = solutions[0]
    k = V.k * to_mm
    pts = [[round(p[0] * k, 9), round(p[1] * k, 9)] for p in poly]

    W = round(total_len * k, 9)
    D_max = round(2.0 * max(p[1] for p in pts), 9)
    area = abs(_poly_area(pts))
    cy = _poly_cy(pts)
    vol = round(2.0 * math.pi * cy * area, 6)

    it.expect = {"size_mm": [D_max, D_max, W], "volume_mm3": vol}
    kind_str = "tube / bushing" if r_inner > 0 else "stepped shaft / cylinder"
    it.facts += [
        Fact("views", len(views), None, "CALCULATED", "edge groups separated by clear space"),
        Fact("turned_part", f"{kind_str}, {W:g} mm long, max diameter {D_max:g} mm" + (f", bore {r_inner * 2 * k:g} mm" if r_inner > 0 else ""),
             None, "CALCULATED", "symmetric profile revolved 360 deg around its centreline"),
        Fact("view_scale_factor", V.k, None, "CALCULATED", f"from {len(V.dims)} dimension(s) in that view" if V.dims else "sheet scale factor"),
        Fact("volume_mm3", vol, "mm^3", "CALCULATED", "Pappus: 2 * pi * centroid_radius * half_profile_area"),
    ]
    stem = "".join(c if c.isalnum() else "_" for c in path.stem)[:40]
    it.spec = {
        "name": f"from_{stem}",
        "units": "mm",
        "features": [
            {"$op": "sketch.profile", "id": "profile", "sketched_on": "front", "args": {"points": pts}},
            {"$op": "feature.revolve", "id": "body", "args": {"angle": 360}}
        ],
        "exports": [{"format": "step"}],
        "notes": {"source_drawing": str(path), "source_sha256": _sha(path)}
    }
    return it


def reconstruct(path: str | Path, *, through: bool = False, scale: float | None = None,
                thickness: float | None = None, features: bool = False,
                app: str | None = "solidworks") -> Interpretation:
    import ezdxf
    path = Path(path)
    it = Interpretation(f"drawing {path.name}", "create_part_from_drawing", app or "solidworks", exports=["step"])
    it.facts.append(Fact("drawing", str(path), None, "USER_SPECIFIED", f"sha256 {_sha(path)}"))
    try:
        doc = ezdxf.readfile(path)
    except Exception as ex:                                  # noqa: BLE001 - reported
        it.missing.append(f"a readable DXF ({type(ex).__name__}: {ex})")
        return it
    problems: list[str] = []
    edges = _read_edges(doc, problems)
    dims = _dims(doc, problems)
    units = doc.header.get("$INSUNITS", 0)
    views = _views(edges, it.facts, [p for d in dims for p in d["pts"]], 25.4 if units in _INCH_UNITS else 1.0)
    if problems:
        it.ambiguities.extend(problems)
        return it
    if not views:
        it.missing.append("drawing views: no lines, arcs or circles were found")
        return it
    units = doc.header.get("$INSUNITS", 0)
    to_mm = 25.4 if units in _INCH_UNITS else 1.0 if units in _MM_UNITS else None
    if to_mm is None:
        it.missing.append(f"the drawing's unit ($INSUNITS {units} is neither mm nor inch)")
        return it
    it.facts.append(Fact("drawing_unit", "inch" if to_mm != 1.0 else "mm", None, "USER_SPECIFIED", f"$INSUNITS {units}"))
    # scale: each view's own dimensions, or one factor for the whole sheet
    for v in views:
        x0, y0, x1, y1 = v.bbox
        pad = 0.02 * max(v.w, v.h)
        v.dims = [d for d in dims if d["pts"] and all(x0 - pad <= p[0] <= x1 + pad and y0 - pad <= p[1] <= y1 + pad
                                                       for p in d["pts"][1:] or d["pts"])]
        ks = {round(d["k"], 9) for d in v.dims}
        if len(ks) > 1:
            it.ambiguities.append(f"the dimensions of one view disagree on its scale ({sorted(ks)})")
            return it
        v.k = next(iter(ks)) if ks else None
    sheet_ks = {round(d["k"], 9) for d in dims}
    stated_scales, stated_thick = _stated(_texts(doc))
    if len({k for _, k in stated_scales}) > 1:
        it.ambiguities.append(f"the drawing states more than one scale: {sorted(s for s, _ in stated_scales)}")
        return it
    stated_k = next(iter(stated_scales))[1] if stated_scales else None
    if stated_k is not None and sheet_ks and any(abs(k - stated_k) > 1e-9 for k in sheet_ks):
        stated_k = None                                      # the dimensions say otherwise: not used
    for v in views:
        if v.k is None:
            if scale is not None:
                v.k = 1.0 / scale
            elif len(sheet_ks) == 1:
                v.k = next(iter(sheet_ks))
            elif stated_k is not None:
                v.k = stated_k
                if not any(f.name == "sheet_scale" for f in it.facts):
                    it.facts.append(Fact("sheet_scale", next(iter(stated_scales))[0], None, "USER_SPECIFIED",
                                         "written on the drawing; used for views without a dimension"))
    if scale is not None:
        it.facts.append(Fact("view_scale", f"1:{1 / scale:g}" if scale < 1 else f"{scale:g}:1", None, "USER_SPECIFIED",
                             "stated for views without a dimension"))
    tol = 1e-6 * max(max(v.w, v.h) for v in views)
    for v in views:
        _loops(v, tol)

    # a thickness the user states, or one the drawing writes ("THK 3"), in mm
    given_depth = None
    if thickness is not None:
        given_depth = (float(thickness), "the thickness you stated")
    elif len({(v, u) for _, v, u in stated_thick}) == 1:
        s, v, u = next(iter(stated_thick))
        given_depth = (v * (25.4 if u in ("IN", '"') else 1.0 if u == "MM" else to_mm), f"the note '{s}'")
    elif len(stated_thick) > 1:
        it.ambiguities.append(f"the drawing states more than one thickness: {sorted(s for s, _, _ in stated_thick)}")
        return it

    # ---- the section view, and the depth from the views aligned with it ----------------------------
    reasons: list[str] = []
    solutions = []
    for P in views:
        if not P.loops:
            continue
        if P.open_edges:
            reasons.append(f"{len(P.open_edges)} edge(s) of a view do not close into loops (a feature seen from the "
                           "side, or one this version does not rebuild)")
            continue
        if any(e.style == "hidden" for e in P.edges):
            reasons.append("the view with the outline also shows hidden edges: a feature on the far side or inside - "
                           "not one constant extrusion")
            continue
        if P.k is None:
            reasons.append("the outline view has no dimension giving its scale")
            continue
        areas = [abs(_area(lp)) for lp in P.loops]
        o = areas.index(max(areas))
        outer, inner = P.loops[o], [lp for i, lp in enumerate(P.loops) if i != o]
        poly = _polygon(outer)
        bad_inner = [lp for lp in inner if not _inside(_polygon(lp)[0], poly)]
        if bad_inner:
            reasons.append("a closed loop lies outside the outline: not one part")
            continue
        ib = [_loop_bbox(lp) for lp in inner]
        if any(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3] for i, a in enumerate(ib) for b in ib[i + 1:]):
            reasons.append("two openings overlap")
            continue
        depths, through_seen, used_views, why = [], {id(lp): 0 for lp in inner}, [], None
        blind_seen: dict[int, list[float]] = {id(lp): [] for lp in inner}
        xs = sorted({round(c, 9) for e, _ in outer for c in (e.bbox()[0], e.bbox()[2])} |
                    {round(c, 9) for lp in inner for c in (_loop_bbox(lp)[0], _loop_bbox(lp)[2])})
        ys = sorted({round(c, 9) for e, _ in outer for c in (e.bbox()[1], e.bbox()[3])} |
                    {round(c, 9) for lp in inner for c in (_loop_bbox(lp)[1], _loop_bbox(lp)[3])})
        for V in views:
            if V is P:
                continue
            vert = abs(V.bbox[0] - P.bbox[0]) <= tol * 10 and abs(V.bbox[2] - P.bbox[2]) <= tol * 10
            horz = abs(V.bbox[1] - P.bbox[1]) <= tol * 10 and abs(V.bbox[3] - P.bbox[3]) <= tol * 10
            if not (vert or horz):
                continue
            if V.k is None:
                why = "an aligned view has no dimension giving its scale"
                break
            # must be a plain rectangle (its sides may be split where other lines meet them), and every
            # other line across it must be at a place the section explains
            boundary, rest = _rectangle(V, tol * 10)
            if boundary is None:
                why = "an aligned view is not a rectangle: the part is not one constant extrusion"
                break
            axis = "x" if vert else "y"
            lo, hi = (V.bbox[1], V.bbox[3]) if vert else (V.bbox[0], V.bbox[2])
            explained = xs if vert else ys
            for e in rest:
                if e.kind != "L":
                    why = "an aligned view has a curved edge: not one constant extrusion"
                    break
                x1, y1, x2, y2 = e.v
                at, a, b = (x1, min(y1, y2), max(y1, y2)) if vert else (y1, min(x1, x2), max(x1, x2))
                along = abs(x1 - x2) <= tol if vert else abs(y1 - y2) <= tol
                if not along:
                    perp = abs(y1 - y2) <= tol * 10 if vert else abs(x1 - x2) <= tol * 10
                    if features and e.style == "hidden" and perp:
                        pos = y1 if vert else x1
                        d_lo = abs(pos - lo)
                        d_hi = abs(pos - hi)
                        floor_depth = min(d_lo, d_hi) * V.k
                        span_a = min(x1, x2) if vert else min(y1, y2)
                        span_b = max(x1, x2) if vert else max(y1, y2)
                        matching = []
                        for lp in inner:
                            bx = _loop_bbox(lp)
                            c0, c1 = (bx[0], bx[2]) if vert else (bx[1], bx[3])
                            if abs(span_a - c0) <= tol * 10 and abs(span_b - c1) <= tol * 10:
                                matching.append(lp)
                        if matching and floor_depth > tol * 10 and floor_depth < (hi - lo) * V.k - tol * 10:
                            for mlp in matching:
                                blind_seen[id(mlp)].append(floor_depth)
                            continue
                    why = "a line across an aligned view is not along the depth direction: not one constant extrusion"
                    break
                is_full = abs(a - lo) <= tol * 10 and abs(b - hi) <= tol * 10
                if not is_full:
                    if not features or e.style != "hidden":
                        why = ("a line in an aligned view does not run the full depth (a step or a blind feature) - "
                               "not one constant extrusion")
                        break
                    touches_face = abs(b - hi) <= tol * 10 or abs(a - lo) <= tol * 10
                    if not touches_face:
                        why = "a hidden line in an aligned view does not touch an outer face: void not supported"
                        break
                    matching = []
                    for lp in inner:
                        bx = _loop_bbox(lp)
                        c0, c1 = (bx[0], bx[2]) if vert else (bx[1], bx[3])
                        if abs(at - c0) <= tol * 10 or abs(at - c1) <= tol * 10:
                            matching.append(lp)
                    if not matching:
                        why = "a line across an aligned view matches nothing in the outline view"
                        break
                    h_depth = (b - a) * V.k
                    for mlp in matching:
                        blind_seen[id(mlp)].append(h_depth)
                else:
                    if not any(abs(at - c) <= tol * 10 for c in explained):
                        why = "a line across an aligned view matches nothing in the outline view"
                        break
            if why:
                break
            depths.append((V.h if vert else V.w) * V.k)
            used_views.append("above/below" if vert else "beside")
            for lp in inner:                                   # hidden lines through the whole depth at its edges
                bx = _loop_bbox(lp)
                c0, c1 = (bx[0], bx[2]) if vert else (bx[1], bx[3])
                sp0 = _span(V, axis, c0, tol * 10)
                sp1 = _span(V, axis, c1, tol * 10)
                def _full_span(sp):
                    for se in sp:
                        sa, sb = (min(se.v[1], se.v[3]), max(se.v[1], se.v[3])) if vert else (min(se.v[0], se.v[2]), max(se.v[0], se.v[2]))
                        if abs(sa - lo) <= tol * 10 and abs(sb - hi) <= tol * 10:
                            return True
                    return False
                if _full_span(sp0) and _full_span(sp1):
                    through_seen[id(lp)] += 1
        if why:
            reasons.append(why)
            continue
        if not depths:
            if given_depth is None:
                reasons.append("no view aligned with the outline view shows the thickness (state it, or the "
                               "drawing's 'THK' note)")
                continue
            depths = [given_depth[0]]
            used_views.append(given_depth[1])
        elif given_depth is not None and abs(given_depth[0] - depths[0] * to_mm) > 1e-6 * depths[0] * to_mm:
            reasons.append(f"the views show a thickness of {depths[0] * to_mm:g} mm but {given_depth[1]} says "
                           f"{given_depth[0]:g} mm")
            continue
        elif given_depth is not None:
            depths = depths                                  # a note or statement that agrees adds nothing
        # every view on the sheet must be explained by the rebuilt part
        if len([u for u in used_views if u in ("above/below", "beside")]) != len(views) - 1:
            reasons.append(f"{len(views) - 1 - len(used_views)} view(s) on the sheet are not aligned with the outline "
                           "view (a detail, section or auxiliary view): the rebuild would not account for them")
            continue
        if max(depths) - min(depths) > 1e-6 * max(depths):
            reasons.append(f"the aligned views disagree on the thickness ({', '.join(f'{d:g}' for d in depths)})")
            continue
        solutions.append((P, outer, inner, depths[0], through_seen, blind_seen, used_views))

    if not solutions:
        res = _reconstruct_turned(views, doc, it, dims, to_mm, scale, through, given_depth, path, reasons)
        if res is not None:
            return res
        it.ambiguities.append("not rebuilt: " + ("; ".join(dict.fromkeys(reasons)) if reasons else
                                                "no view closes into an outline"))
        return it
    P, outer, inner, depth_sheet_true, through_seen, blind_seen, used_views = max(solutions, key=lambda s: len(s[2]))
    any_hidden = any(e.style == "hidden" for v in views for e in v.edges)
    through_inner = []
    blind_inner = []
    unproven = []
    for lp in inner:
        if through_seen[id(lp)] > 0:
            through_inner.append(lp)
        elif id(lp) in blind_seen and blind_seen[id(lp)]:
            d_list = blind_seen[id(lp)]
            d_val = sum(d_list) / len(d_list)
            if any(abs(d - d_val) > tol * 10 for d in d_list):
                it.ambiguities.append("aligned views disagree on the depth of a blind opening")
                return it
            blind_inner.append((lp, d_val))
        else:
            unproven.append(lp)

    if unproven:
        if any_hidden:
            it.ambiguities.append(f"{len(unproven)} opening(s) are not shown through the whole thickness in the "
                                  "aligned views: blind features are not rebuilt")
            return it
        if not through:
            it.ambiguities.append(f"the drawing shows no hidden lines, so whether its {len(unproven)} opening(s) go "
                                  "through is not shown - confirm 'through' to rebuild them as through openings")
            return it
        through_inner.extend(unproven)
        it.facts.append(Fact("openings_through", True, None, "USER_SPECIFIED",
                             "stated by the user; the drawing has no hidden lines to show it"))
    elif through_inner and not blind_inner:
        it.facts.append(Fact("openings_through", True, None, "CALCULATED",
                             f"each of {len(through_inner)} opening(s) has hidden lines through the whole depth in the aligned views"))
    if blind_inner:
        it.facts.append(Fact("openings_through", False, None, "CALCULATED",
                             f"{len(blind_inner)} opening(s) have blind depth shown by hidden lines in the aligned views"))

    k = P.k * to_mm
    ox, oy = P.bbox[0], P.bbox[1]
    T = lambda x, y: (round((x - ox) * k, 9), round((y - oy) * k, 9))      # noqa: E731

    def emit(loop):
        segs = []
        for e, rev in loop:
            if e.kind == "L":
                (a, b), (c, d) = (e.ends[::-1] if rev else e.ends)
                segs.append(["L", *T(a, b), *T(c, d)])
            elif e.kind == "C":
                segs.append(["C", *T(e.v[0], e.v[1]), round(e.v[2] * k, 9)])
            else:
                s, en = e.ends
                (s, en), d = ((en, s), -1) if rev else ((s, en), 1)
                segs.append(["A", *T(e.v[0], e.v[1]), *T(*s), *T(*en), d])
        return segs
    loops = [emit(outer)] + [emit(lp) for lp in through_inner]
    from_views = any(u in ("above/below", "beside") for u in used_views)
    depth = round(depth_sheet_true * to_mm if from_views else depth_sheet_true, 9)
    base_net = (abs(_area(outer)) - sum(abs(_area(lp)) for lp in through_inner)) * k * k
    blind_vol = sum(abs(_area(lp)) * k * k * (bd * to_mm if from_views else bd) for lp, bd in blind_inner)
    net_vol = round(base_net * depth - blind_vol, 6)
    W, H = round(P.w * k, 9), round(P.h * k, 9)
    n_arcs = sum(1 for lp in [outer] + inner for e, _ in lp if e.kind == "A")
    it.facts += [
        Fact("views", len(views), None, "CALCULATED", "edge groups separated by clear space"),
        Fact("outline_view", f"{W:g} x {H:g} mm, {len(outer)} edge(s) ({n_arcs} arc(s) in all loops), "
             f"{len(inner)} opening(s)", None, "CALCULATED", "the one view whose visible edges close into loops"),
        Fact("view_scale_factor", P.k, None, "CALCULATED",
             f"from {len(P.dims)} dimension(s) in that view" if P.dims else "the one scale all the sheet's dimensions share"),
        Fact("thickness_mm", depth, "mm", "CALCULATED" if from_views else "USER_SPECIFIED",
             f"from {len(used_views)} aligned view(s) ({', '.join(used_views)}), all equal" if from_views
             else f"from {used_views[0]} (no view shows it)"),
        Fact("volume_mm3", net_vol, "mm^3", "CALCULATED", "net section area x thickness less blind hole volumes"),
    ]
    it.expect = {"size_mm": [W, H, depth], "volume_mm3": net_vol}
    stem = "".join(c if c.isalnum() else "_" for c in path.stem)[:40]
    notes_dict = {"source_drawing": str(path), "source_sha256": _sha(path)}
    if blind_inner:
        b_holes = []
        b_pockets = []
        for lp, bd in blind_inner:
            segs = emit(lp)
            circ = _is_circular_loop_segs(segs)
            if circ is not None:
                cx, cy, r = circ
                b_holes.append({"x": cx, "y": cy, "d": round(2 * r, 9), "depth": round(bd * to_mm if from_views else bd, 9)})
            else:
                rect = _is_rectangular_loop_segs(segs)
                if rect is not None:
                    cx, cy, pw, ph = rect
                    b_pockets.append({"x": cx, "y": cy, "width": round(pw, 9), "height": round(ph, 9),
                                      "depth": round(bd * to_mm if from_views else bd, 9)})
        if b_holes:
            notes_dict["blind_holes"] = b_holes
        if b_pockets:
            notes_dict["blind_pockets"] = b_pockets
    it.spec = {"name": f"from_{stem}", "units": "mm",
               "features": [{"$op": "sketch.loops", "id": "section", "sketched_on": "front", "args": {"loops": loops}},
                            {"$op": "feature.extrude", "id": "body", "args": {"depth": depth}}],
               "exports": [{"format": "step"}],
               "notes": notes_dict}
    if features:
        it = recognize_features(it)
    return it


def _is_rectangular_loop_segs(lp: list) -> tuple[float, float, float, float] | None:
    """Return (cx, cy, width, height) if loop segments form an axis-aligned rectangle."""
    if len(lp) < 4:
        return None
    if any(s[0] != "L" for s in lp):
        return None
    xs = []
    ys = []
    for s in lp:
        _, x1, y1, x2, y2 = s
        is_horz = abs(y1 - y2) < 1e-5
        is_vert = abs(x1 - x2) < 1e-5
        if not (is_horz or is_vert):
            return None
        xs.extend([x1, x2])
        ys.extend([y1, y2])
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    w = max_x - min_x
    h = max_y - min_y
    if w < 1e-5 or h < 1e-5:
        return None
    perim = sum(math.hypot(s[3] - s[1], s[4] - s[2]) for s in lp)
    if abs(perim - 2 * (w + h)) > 1e-4:
        return None
    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    return (round(cx, 9), round(cy, 9), round(w, 9), round(h, 9))


def _is_circular_loop_segs(lp: list) -> tuple[float, float, float] | None:
    """Return (cx, cy, r) if loop segments form a circle."""
    if len(lp) == 1 and lp[0][0] == "C":
        return (lp[0][1], lp[0][2], lp[0][3])
    if len(lp) == 2 and lp[0][0] == "A" and lp[1][0] == "A":
        cx1, cy1, r1 = lp[0][1], lp[0][2], lp[0][3]
        cx2, cy2, r2 = lp[1][1], lp[1][2], lp[1][3]
        if abs(cx1 - cx2) < 1e-6 and abs(cy1 - cy2) < 1e-6 and abs(r1 - r2) < 1e-6:
            return (cx1, cy1, r1)
    return None


def _find_linear_patterns(holes: list[dict], depth: float, start_hole_id: int, start_pat_id: int) -> tuple[list[dict], list[dict], int, int]:
    """Find linear patterns (horizontal or vertical) in a list of holes of equal diameter."""
    used = set()
    features = []
    hole_id = start_hole_id
    pat_id = start_pat_id

    # Horizontal rows (same Y, sorted by X)
    by_y: dict[float, list[int]] = {}
    for i, h in enumerate(holes):
        matched_y = None
        for yk in by_y:
            if abs(h["y"] - yk) < 1e-5:
                matched_y = yk
                break
        if matched_y is None:
            matched_y = h["y"]
            by_y[matched_y] = []
        by_y[matched_y].append(i)

    for yk, idxs in by_y.items():
        if len(idxs) >= 2:
            s_idxs = sorted(idxs, key=lambda i: holes[i]["x"])
            xs = [holes[i]["x"] for i in s_idxs]
            spacings = [xs[j + 1] - xs[j] for j in range(len(xs) - 1)]
            if len(spacings) >= 1 and all(s > 1e-5 and abs(s - spacings[0]) < 1e-5 for s in spacings):
                seed = holes[s_idxs[0]]
                seed_id = f"hole{hole_id}"
                hole_id += 1
                features.append({
                    "$op": "feature.hole", "id": seed_id, "sketched_on": "front",
                    "args": {"diameter": seed["d"], "depth": depth, "center": [seed["x"], seed["y"]]}
                })
                features.append({
                    "$op": "feature.pattern.linear", "id": f"pattern{pat_id}",
                    "args": {"feature": seed_id, "count": len(s_idxs), "spacing": round(spacings[0], 9), "direction": "X"}
                })
                pat_id += 1
                used.update(s_idxs)

    # Vertical columns (same X, sorted by Y)
    remaining_idxs = [i for i in range(len(holes)) if i not in used]
    by_x: dict[float, list[int]] = {}
    for i in remaining_idxs:
        h = holes[i]
        matched_x = None
        for xk in by_x:
            if abs(h["x"] - xk) < 1e-5:
                matched_x = xk
                break
        if matched_x is None:
            matched_x = h["x"]
            by_x[matched_x] = []
        by_x[matched_x].append(i)

    for xk, idxs in by_x.items():
        if len(idxs) >= 2:
            s_idxs = sorted(idxs, key=lambda i: holes[i]["y"])
            ys = [holes[i]["y"] for i in s_idxs]
            spacings = [ys[j + 1] - ys[j] for j in range(len(ys) - 1)]
            if len(spacings) >= 1 and all(s > 1e-5 and abs(s - spacings[0]) < 1e-5 for s in spacings):
                seed = holes[s_idxs[0]]
                seed_id = f"hole{hole_id}"
                hole_id += 1
                features.append({
                    "$op": "feature.hole", "id": seed_id, "sketched_on": "front",
                    "args": {"diameter": seed["d"], "depth": depth, "center": [seed["x"], seed["y"]]}
                })
                features.append({
                    "$op": "feature.pattern.linear", "id": f"pattern{pat_id}",
                    "args": {"feature": seed_id, "count": len(s_idxs), "spacing": round(spacings[0], 9), "direction": "Y"}
                })
                pat_id += 1
                used.update(s_idxs)

    unused = [holes[i] for i in range(len(holes)) if i not in used]
    return features, unused, hole_id, pat_id


def _find_circular_patterns(holes: list[dict], depth: float, start_hole_id: int, start_pat_id: int) -> tuple[list[dict], list[dict], int, int]:
    """Find circular bolt patterns in a list of holes of equal diameter."""
    if len(holes) < 3:
        return [], holes, start_hole_id, start_pat_id
    m = len(holes)
    cx = sum(h["x"] for h in holes) / m
    cy = sum(h["y"] for h in holes) / m
    radii = [math.hypot(h["x"] - cx, h["y"] - cy) for h in holes]
    r0 = radii[0]
    if r0 > 1e-4 and all(abs(r - r0) < 1e-4 for r in radii):
        angles = [(math.atan2(h["y"] - cy, h["x"] - cx) % (2 * math.pi), h) for h in holes]
        angles.sort(key=lambda t: t[0])
        expected_step = 2 * math.pi / m
        diffs = [(angles[(i + 1) % m][0] - angles[i][0]) % (2 * math.pi) for i in range(m)]
        if all(abs(d - expected_step) < 1e-4 for d in diffs):
            seed = angles[0][1]
            seed_id = f"hole{start_hole_id}"
            features = [
                {
                    "$op": "feature.hole", "id": seed_id, "sketched_on": "front",
                    "args": {"diameter": seed["d"], "depth": depth, "center": [seed["x"], seed["y"]]}
                },
                {
                    "$op": "feature.pattern.circular", "id": f"pattern{start_pat_id}",
                    "args": {"feature": seed_id, "count": m, "axis": "Z", "center": [round(cx, 9), round(cy, 9)]}
                }
            ]
            return features, [], start_hole_id + 1, start_pat_id + 1
    return [], holes, start_hole_id, start_pat_id


def recognize_features(it: Interpretation) -> Interpretation:
    """Recognise circular cutouts in sketch.loops (and any blind holes) as Hole and Pattern features.

    When a 2D drawing contains circular holes, this pass separates them from the base
    sketch.loops, keeping only the outline (and any non-circular cutouts) in the base
    extrusion, and appends real feature.hole and feature.pattern.linear / circular features.
    The expected volume and surface area remain analytically identical.
    """
    if not it.executable or not it.spec or "features" not in it.spec:
        return it
    feats = it.spec["features"]
    loops_idx = next((i for i, f in enumerate(feats) if f.get("$op") == "sketch.loops"), None)
    ext_idx = next((i for i, f in enumerate(feats) if f.get("$op") == "feature.extrude"), None)
    if loops_idx is None or ext_idx is None:
        return it
    loops = feats[loops_idx]["args"].get("loops", [])
    blind_holes = it.spec.get("notes", {}).get("blind_holes", [])
    blind_pockets = it.spec.get("notes", {}).get("blind_pockets", [])
    if len(loops) <= 1 and not blind_holes and not blind_pockets:
        return it
    outer = loops[0]
    inner = loops[1:] if len(loops) > 1 else []
    depth = feats[ext_idx]["args"]["depth"]

    circular_holes = []
    other_inner = []
    for lp in inner:
        circ = _is_circular_loop_segs(lp)
        if circ is not None:
            cx, cy, r = circ
            circular_holes.append({"x": round(cx, 9), "y": round(cy, 9), "d": round(2 * r, 9), "depth": depth})
        else:
            other_inner.append(lp)

    for bh in blind_holes:
        circular_holes.append({"x": round(bh["x"], 9), "y": round(bh["y"], 9), "d": round(bh["d"], 9), "depth": round(bh["depth"], 9)})

    new_feats = []
    hole_id = 1
    pat_id = 1
    total_recognized = 0

    if circular_holes:
        by_dia_depth: dict[tuple[float, float], list[dict]] = {}
        for h in circular_holes:
            by_dia_depth.setdefault((round(h["d"], 6), round(h["depth"], 6)), []).append(h)

        for (dia, h_depth), group in sorted(by_dia_depth.items()):
            c_feats, rem_c, hole_id, pat_id = _find_circular_patterns(group, h_depth, hole_id, pat_id)
            if c_feats:
                new_feats.extend(c_feats)
                total_recognized += len(group)
                continue
            l_feats, rem_l, hole_id, pat_id = _find_linear_patterns(group, h_depth, hole_id, pat_id)
            new_feats.extend(l_feats)
            total_recognized += len(group) - len(rem_l)
            for h in rem_l:
                new_feats.append({
                    "$op": "feature.hole", "id": f"hole{hole_id}", "sketched_on": "front",
                    "args": {"diameter": h["d"], "depth": h_depth, "center": [h["x"], h["y"]]}
                })
                hole_id += 1
                total_recognized += 1

    pocket_count = 0
    for i, bp in enumerate(blind_pockets, start=1):
        new_feats.extend([
            {
                "$op": "sketch.rectangle", "id": f"pocket{i}_sk", "sketched_on": "front",
                "args": {"x": bp["x"], "y": bp["y"], "width": bp["width"], "height": bp["height"]}
            },
            {
                "$op": "feature.cut", "id": f"pocket{i}",
                "args": {"depth": bp["depth"]}
            }
        ])
        pocket_count += 1

    if not new_feats:
        return it

    feats[loops_idx]["args"]["loops"] = [outer] + other_inner
    feats[ext_idx + 1:ext_idx + 1] = new_feats
    parts_desc = []
    if total_recognized:
        parts_desc.append(f"{total_recognized} hole(s)")
    if pocket_count:
        parts_desc.append(f"{pocket_count} pocket(s)")
    it.facts.append(Fact("recognized_features",
                         f"{' and '.join(parts_desc)} recognized into {len(new_feats)} feature(s)",
                         None, "CALCULATED",
                         "inner loops and blind openings converted to parametric features"))
    return it


def describe(it: Interpretation) -> dict[str, Any]:
    return it.to_dict()
