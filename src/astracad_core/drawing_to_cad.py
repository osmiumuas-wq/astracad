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
    if any(c in lt for c in _CENTER):
        return "center"
    if any(c in lt for c in _HIDDEN):
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
        if len(set(r2)) < len(gl):
            merged = True
            groups = {}
            for g, r in zip(gl, r2):
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
        out.append({"handle": e.dxf.handle, "k": shown / meas, "value": shown, "pts": pts})
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


def reconstruct(path: str | Path, *, through: bool = False, scale: float | None = None,
                thickness: float | None = None, app: str | None = "solidworks") -> Interpretation:
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
                if not along or abs(a - lo) > tol * 10 or abs(b - hi) > tol * 10:
                    why = ("a line in an aligned view does not run the full depth (a step or a blind feature) - "
                           "not one constant extrusion")
                    break
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
                if _span(V, axis, c0, tol * 10) and _span(V, axis, c1, tol * 10):
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
        solutions.append((P, outer, inner, depths[0], through_seen, used_views))

    if not solutions:
        it.ambiguities.append("not rebuilt: " + ("; ".join(dict.fromkeys(reasons)) if reasons else
                                                "no view closes into an outline"))
        return it
    P, outer, inner, depth_sheet_true, through_seen, used_views = max(solutions, key=lambda s: len(s[2]))
    any_hidden = any(e.style == "hidden" for v in views for e in v.edges)
    unproven = [lp for lp in inner if through_seen[id(lp)] == 0]
    if unproven:
        if any_hidden:
            it.ambiguities.append(f"{len(unproven)} opening(s) are not shown through the whole thickness in the "
                                  "aligned views: blind features are not rebuilt")
            return it
        if not through:
            it.ambiguities.append(f"the drawing shows no hidden lines, so whether its {len(unproven)} opening(s) go "
                                  "through is not shown - confirm 'through' to rebuild them as through openings")
            return it
        it.facts.append(Fact("openings_through", True, None, "USER_SPECIFIED",
                             "stated by the user; the drawing has no hidden lines to show it"))
    elif inner:
        it.facts.append(Fact("openings_through", True, None, "CALCULATED",
                             f"each of {len(inner)} opening(s) has hidden lines through the whole depth in the aligned views"))

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
    loops = [emit(outer)] + [emit(lp) for lp in inner]
    from_views = any(u in ("above/below", "beside") for u in used_views)
    depth = round(depth_sheet_true * to_mm if from_views else depth_sheet_true, 9)
    net = (abs(_area(outer)) - sum(abs(_area(lp)) for lp in inner)) * k * k
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
        Fact("volume_mm3", net * depth, "mm^3", "CALCULATED", "net section area (arcs exact) x thickness"),
    ]
    it.expect = {"size_mm": [W, H, depth], "volume_mm3": net * depth}
    stem = "".join(c if c.isalnum() else "_" for c in path.stem)[:40]
    it.spec = {"name": f"from_{stem}", "units": "mm",
               "features": [{"$op": "sketch.loops", "id": "section", "sketched_on": "front", "args": {"loops": loops}},
                            {"$op": "feature.extrude", "id": "body", "args": {"depth": depth}}],
               "exports": [{"format": "step"}],
               "notes": {"source_drawing": str(path), "source_sha256": _sha(path)}}
    return it


def describe(it: Interpretation) -> dict[str, Any]:
    return it.to_dict()
