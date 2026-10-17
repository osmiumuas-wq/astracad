"""Kernel-independent re-reading of an exported STEP (ISO 10303-21) file.

The CAD system that wrote the file is not asked anything here. The file is parsed as text and the
B-rep is re-evaluated with plain vector arithmetic:

- header: schema, originating system, file name
- length unit (SI_UNIT with prefix, or a CONVERSION_BASED_UNIT such as INCH)
- solid and face counts
- an axis-aligned bounding box from vertices and the exact angular extremes of full and partial
  circular edges (marked ``approximate`` only when a curve type other than LINE/CIRCLE is present)
- the enclosed volume, **only** when every face is a PLANE bounded by LINE edges (a polyhedron);
  otherwise volume is NOT_VERIFIABLE with the reason, never estimated.

Volume method: divergence theorem over oriented loops, V = 1/3 * sum(p0 . A_loop), where A_loop is
the loop's vector area oriented with the face's outward normal for outer bounds and against it for
inner bounds. The orientation is derived from the plane normal and ADVANCED_FACE.same_sense, and
cross-checked: a negative total is reported as a failure rather than silently flipped.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

_ENTITY = re.compile(r"#(\d+)\s*=\s*(.*?);\s*(?=#\d+\s*=|ENDSEC)", re.S)

Vec = tuple[float, float, float]


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _split_args(s: str) -> list[str]:
    """Split a STEP parameter list at top-level commas, respecting parentheses and quotes."""
    out, depth, cur, quote = [], 0, [], False
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == "'":
                quote = False
            continue
        if ch == "'":
            quote = True
            cur.append(ch)
        elif ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur or out:
        out.append("".join(cur).strip())
    return out


@dataclass
class Entity:
    eid: int
    kind: str          # first entity name; complex entities keep the raw text in ``raw``
    args: list[str]
    raw: str


def _parse(text: str) -> dict[int, Entity]:
    data = text.split("DATA;", 1)
    if len(data) != 2:
        raise ValueError("no DATA section")
    ents: dict[int, Entity] = {}
    for m in _ENTITY.finditer(data[1]):
        eid, body = int(m.group(1)), m.group(2).strip()
        if body.startswith("("):            # complex entity, e.g. ( LENGTH_UNIT() SI_UNIT(...) )
            ents[eid] = Entity(eid, "COMPLEX", [], body)
            continue
        k = body.find("(")
        name = body[:k].strip()
        inner = body[k + 1: body.rfind(")")]
        ents[eid] = Entity(eid, name, _split_args(inner), body)
    return ents


def _ref(s: str) -> int:
    s = s.strip()
    if not s.startswith("#"):
        raise ValueError(f"expected reference, got {s!r}")
    return int(s[1:])


def _refs(s: str) -> list[int]:
    return [int(x) for x in re.findall(r"#(\d+)", s)]


def _floats(s: str) -> list[float]:
    return [float(x) for x in re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", s)]


@dataclass
class StepReport:
    path: str
    schema: str | None = None
    originating_system: str | None = None
    length_unit: str | None = None
    mm_per_unit: float | None = None
    entity_count: int = 0
    solids: int = 0
    faces: int = 0
    face_surface_types: dict[str, int] = field(default_factory=dict)
    edge_curve_types: dict[str, int] = field(default_factory=dict)
    bbox_mm: list[float] | None = None
    bbox_approximate: bool = False
    volume_mm3: float | None = None
    volume_reason: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def size_mm(self) -> list[float] | None:
        if self.bbox_mm is None:
            return None
        b = self.bbox_mm
        return [b[3] - b[0], b[4] - b[1], b[5] - b[2]]


_PREFIX = {"$": 1.0, ".MILLI.": 1e-3, ".CENTI.": 1e-2, ".DECI.": 1e-1, ".KILO.": 1e3,
           ".MICRO.": 1e-6}


def read_step(path: str | Path, max_bytes: int = 200 * 1024 * 1024) -> StepReport:
    p = Path(path)
    rep = StepReport(str(p))
    size = p.stat().st_size
    if size > max_bytes:
        rep.errors.append(f"file is {size} B, above the {max_bytes} B read limit")
        return rep
    text = p.read_text(encoding="latin-1")
    if not text.lstrip().startswith("ISO-10303-21;"):
        rep.errors.append("missing ISO-10303-21 signature")
        return rep
    header = text.split("ENDSEC;", 1)[0]
    m = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", header)
    rep.schema = m.group(1) if m else None
    m = re.search(r"FILE_NAME\s*\((.*?)\)\s*;", header, re.S)
    if m:
        fargs = _split_args(m.group(1))
        if len(fargs) >= 6:
            rep.originating_system = fargs[5].strip("'") or None
    try:
        ents = _parse(text)
    except ValueError as ex:
        rep.errors.append(str(ex))
        return rep
    rep.entity_count = len(ents)

    # ---- length unit ------------------------------------------------------------------------
    for e in ents.values():
        if e.kind == "COMPLEX" and "LENGTH_UNIT" in e.raw:
            si = re.search(r"SI_UNIT\s*\(\s*([^,]+?)\s*,\s*\.METRE\.\s*\)", e.raw)
            if si:
                prefix = si.group(1).strip()
                rep.mm_per_unit = _PREFIX.get(prefix, None)
                if rep.mm_per_unit is not None:
                    rep.mm_per_unit *= 1000.0
                rep.length_unit = {".MILLI.": "mm", "$": "m", ".CENTI.": "cm"}.get(prefix, prefix)
            conv = re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']+)'\s*,\s*#(\d+)", e.raw)
            if conv:
                rep.length_unit = conv.group(1).lower()
                if rep.length_unit in ("inch", "in"):
                    rep.mm_per_unit = 25.4
            break
    if rep.mm_per_unit is None:
        rep.errors.append("length unit not found or not recognised; geometry not scaled")
        return rep
    k = rep.mm_per_unit

    def point(eid: int) -> Vec:
        e = ents[eid]
        if e.kind != "CARTESIAN_POINT":
            raise ValueError(f"#{eid} is {e.kind}, not CARTESIAN_POINT")
        xs = _floats(e.args[1])
        return (xs[0] * k, xs[1] * k, xs[2] * k if len(xs) > 2 else 0.0)

    def direction(eid: int) -> Vec:
        xs = _floats(ents[eid].args[1])
        n = math.sqrt(sum(x * x for x in xs)) or 1.0
        return (xs[0] / n, xs[1] / n, xs[2] / n)

    def placement(eid: int) -> tuple[Vec, Vec]:
        e = ents[eid]        # AXIS2_PLACEMENT_3D('', location, axis, ref)
        return point(_ref(e.args[1])), direction(_ref(e.args[2]))

    def vertex(eid: int) -> Vec:
        return point(_ref(ents[eid].args[1]))

    rep.solids = sum(1 for e in ents.values()
                     if e.kind in ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS"))
    faces = [e for e in ents.values() if e.kind in ("ADVANCED_FACE", "FACE_SURFACE")]
    rep.faces = len(faces)
    for f in faces:
        st = ents[_ref(f.args[2])].kind
        rep.face_surface_types[st] = rep.face_surface_types.get(st, 0) + 1
    edges = [e for e in ents.values() if e.kind == "EDGE_CURVE"]
    for ed in edges:
        ct = ents[_ref(ed.args[3])].kind
        rep.edge_curve_types[ct] = rep.edge_curve_types.get(ct, 0) + 1

    # ---- bounding box -----------------------------------------------------------------------
    pts: list[Vec] = [vertex(e.eid) for e in ents.values() if e.kind == "VERTEX_POINT"]
    for ed in edges:
        curve = ents[_ref(ed.args[3])]
        if curve.kind == "CIRCLE":
            pl = ents[_ref(curve.args[1])]
            c, n = placement(_ref(curve.args[1]))
            r = float(curve.args[2]) * k
            refd = direction(_ref(pl.args[3])) if len(pl.args) > 3 and pl.args[3].strip() != "$" \
                else None
            v0, v1 = _ref(ed.args[1]), _ref(ed.args[2])
            full = v0 == v1
            # in-plane frame: x = ref direction made orthogonal to the axis, y = axis x x
            if refd is None:
                refd = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
            d = _dot(refd, n)
            x = (refd[0] - d * n[0], refd[1] - d * n[1], refd[2] - d * n[2])
            xl = math.sqrt(_dot(x, x)) or 1.0
            x = (x[0] / xl, x[1] / xl, x[2] / xl)
            y = _cross(n, x)

            def ang(p: Vec) -> float:
                q = _sub(p, c)
                return math.atan2(_dot(q, y), _dot(q, x)) % (2 * math.pi)

            if full:
                lo_a, span = 0.0, 2 * math.pi
            else:
                a0, a1 = ang(vertex(v0)), ang(vertex(v1))
                # STEP circles are parametrised counter-clockwise about the axis; an edge used
                # with same_sense = .F. runs from end to start
                if ed.args[4].strip() != ".T.":
                    a0, a1 = a1, a0
                lo_a, span = a0, (a1 - a0) % (2 * math.pi) or 2 * math.pi
                pts.extend([vertex(v0), vertex(v1)])
            for i in range(3):
                # extremes of coordinate i on the circle: angle where d(coord_i)/dθ = 0
                th = math.atan2(y[i], x[i])
                for t in (th, th + math.pi):
                    if full or (t - lo_a) % (2 * math.pi) <= span + 1e-12:
                        pts.append((c[0] + r * (math.cos(t) * x[0] + math.sin(t) * y[0]),
                                    c[1] + r * (math.cos(t) * x[1] + math.sin(t) * y[1]),
                                    c[2] + r * (math.cos(t) * x[2] + math.sin(t) * y[2])))
        elif curve.kind != "LINE":
            rep.bbox_approximate = True
    if pts:
        rep.bbox_mm = [min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts),
                       max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts)]

    # ---- polyhedral volume ------------------------------------------------------------------
    non_planar = {t for t in rep.face_surface_types if t != "PLANE"}
    non_line = {t for t in rep.edge_curve_types if t != "LINE"}
    if rep.solids != 1:
        rep.volume_reason = f"{rep.solids} solids; volume is computed for exactly one solid"
    elif non_planar or non_line:
        rep.volume_reason = ("not a polyhedron: surfaces " + ",".join(sorted(non_planar) or ["-"])
                             + "; curves " + ",".join(sorted(non_line) or ["-"])
                             + " — exact volume needs a geometry kernel")
    else:
        try:
            total = 0.0
            for f in faces:
                surf = ents[_ref(f.args[2])]
                _, normal = placement(_ref(surf.args[1]))
                same = f.args[3].strip() == ".T."
                outward = normal if same else (-normal[0], -normal[1], -normal[2])
                for b in _refs(f.args[1]):
                    bound = ents[b]
                    is_outer = bound.kind == "FACE_OUTER_BOUND"
                    loop = ents[_ref(bound.args[1])]
                    poly: list[Vec] = []
                    for oe_id in _refs(loop.args[1]):
                        oe = ents[oe_id]
                        ec = ents[_ref(oe.args[3])]
                        start, end = _ref(ec.args[1]), _ref(ec.args[2])
                        fwd = (oe.args[4].strip() == ".T.") == (ec.args[4].strip() == ".T.")
                        poly.append(vertex(start if fwd else end))
                    area = (0.0, 0.0, 0.0)
                    for i in range(len(poly)):
                        c = _cross(poly[i], poly[(i + 1) % len(poly)])
                        area = (area[0] + c[0] / 2, area[1] + c[1] / 2, area[2] + c[2] / 2)
                    want = 1.0 if is_outer else -1.0
                    if _dot(area, outward) * want < 0:
                        area = (-area[0], -area[1], -area[2])
                    total += _dot(poly[0], area) / 3.0
            if total <= 0:
                rep.errors.append(f"polyhedral volume came out {total:.6g}; orientation inconsistent")
            else:
                rep.volume_mm3 = total
        except (ValueError, KeyError, IndexError) as ex:
            rep.volume_reason = f"B-rep could not be walked: {ex}"
    return rep
