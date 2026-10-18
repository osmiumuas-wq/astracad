"""Build a Part Language spec in OpenCascade - the second kernel (docs/engineering/part-language-v2.md).

Built from the spec alone, never from anything SOLIDWORKS reports, so agreement between the two kernels is evidence
that SOLIDWORKS built what the spec says. Every operation follows the language document; where this builder cannot
build something yet it raises NotSupported (the comparison is then "not verifiable", never "passed").

v1 operations (all) are implemented here; v2 operations are added in M1.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepLib import BRepLib
from OCP.BRepOffset import BRepOffset_Skin
from OCP.BRepOffsetAPI import (BRepOffsetAPI_MakePipe, BRepOffsetAPI_MakePipeShell, BRepOffsetAPI_MakeThickSolid,
                               BRepOffsetAPI_ThruSections)
from OCP.BRepTools import BRepTools
from OCP.Geom import Geom_CylindricalSurface
from OCP.Geom2d import Geom2d_Line
from OCP.GeomAbs import GeomAbs_Intersection
from OCP.collections import List_TopoDS_Shape
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire,
                                BRepBuilderAPI_Transform)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.Bnd import Bnd_Box
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Dir2d, gp_Pnt, gp_Pnt2d, gp_Trsf, gp_Vec

from . import frames as F
from . import queries as Q

LIN_TOL = 1e-6          # mm: coincidence of points, "parallel to Z" etc.
V1_OPS = {"sketch.rectangle", "sketch.circle", "sketch.loops", "sketch.profile", "feature.extrude", "feature.cut",
          "feature.revolve", "feature.hole", "feature.fillet", "feature.chamfer", "feature.pattern.linear",
          "feature.pattern.circular", "feature.parameter", "doc.note"}
SKETCH_OPS = {"sketch.rectangle", "sketch.circle", "sketch.loops", "sketch.profile"}


class SpecError(ValueError):
    """Not valid Part Language: the same spec must be refused by `mac` too."""


class NotSupported(Exception):
    """Valid Part Language this builder cannot build yet; comparisons become not verifiable."""


class BuildError(RuntimeError):
    """OpenCascade could not build a valid spec (a kernel limitation or a geometric impossibility)."""


class QueryMismatch(BuildError):
    """A query found a different number of edges or faces than the spec expects (the step fails)."""


CLASS_ORDER = "EBF"          # verification classes, tightest first (part-language-v2.md section 6)

@dataclass
class Sketch:
    id: str
    frame: F.Frame
    loops: list
    kind: str


@dataclass
class Tool:
    """What a solid feature did, so patterns and mirrors can apply it again."""
    shape: Any
    op: str            # "add" | "cut"
    body: str


@dataclass
class Built:
    shape: Any                                  # the part: one solid, or a compound when several bodies remain
    bodies: dict[str, Any]
    log: list[str] = field(default_factory=list)
    tolerance_class: str = "E"


# ---- small OCC helpers ----------------------------------------------------------------------------

def _p(v) -> gp_Pnt:
    return gp_Pnt(float(v[0]), float(v[1]), float(v[2]))


def _d(v) -> gp_Dir:
    return gp_Dir(float(v[0]), float(v[1]), float(v[2]))


def _translated(shape, d):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(float(d[0]), float(d[1]), float(d[2])))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def _rotated(shape, origin, direction, angle_rad):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(_p(origin), _d(direction)), angle_rad)
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def _unify(shape):
    u = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
    u.Build()
    return u.Shape()


def _boolean(kind: str, a, b, what: str):
    if kind in ("add", "fuse"):
        op = BRepAlgoAPI_Fuse(a, b)
    elif kind in ("cut", "subtract"):
        op = BRepAlgoAPI_Cut(a, b)
    elif kind == "intersect":
        op = BRepAlgoAPI_Common(a, b)
    else:
        raise SpecError(f"{what}: unknown boolean {kind!r}")
    if not op.IsDone() or op.Shape().IsNull():
        raise BuildError(f"{what}: OpenCascade boolean {kind} failed")
    return _unify(op.Shape())


def edges_of(shape) -> list:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, m)
    return [TopoDS.Edge(m.FindKey(i)) for i in range(1, m.Extent() + 1)]


def bbox(shape) -> list[float]:
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    lo, hi = b.CornerMin(), b.CornerMax()
    return [lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z()]


def _on_arc(seg, theta: float) -> bool:
    """Whether the direction theta (radians) from the arc's centre lies within the arc's sweep."""
    t0, d = math.atan2(seg[4] - seg[2], seg[3] - seg[1]), F.arc_sweep(seg)
    rel = (theta - t0) % (2 * math.pi)
    return rel <= d + 1e-12 if d > 0 else (rel == 0 or rel >= 2 * math.pi + d - 1e-12)


def _edge_points(e):
    a = BRep_Tool.Pnt_s(TopExp.FirstVertex_s(e))
    b = BRep_Tool.Pnt_s(TopExp.LastVertex_s(e))
    return (a.X(), a.Y(), a.Z()), (b.X(), b.Y(), b.Z())


# ---- the builder ------------------------------------------------------------------------------------

class Builder:
    def __init__(self, spec: dict):
        if not isinstance(spec, dict) or not isinstance(spec.get("features"), list) or not spec["features"]:
            raise SpecError("the spec has no list of features")
        if str(spec.get("units", "mm")).lower() != "mm":
            raise NotSupported("only mm specs are built here")
        self.spec = copy.deepcopy(spec)
        self.language = int(self.spec.get("language", 1))
        self.frames: dict[str, F.Frame] = dict(F.NAMED)
        self.sketches: dict[str, Sketch] = {}
        self.pending: str | None = None
        self.unused: list[str] = []             # sketches not used yet, oldest first
        self.bodies: dict[str, Any] = {}
        self.tools: dict[str, Tool] = {}
        self.log: list[str] = []
        self.tolerance_class = "E"
        self._apply_drives()

    # parameters: a `drives` binding is applied to the spec before building, exactly as mac's ExpectationEngine
    # analyses the final state; a parameter without one names a SOLIDWORKS dimension this kernel cannot see
    def _apply_drives(self):
        for f in self.spec["features"]:
            if not isinstance(f, dict) or f.get("$op") != "feature.parameter":
                continue
            drives = (f.get("args") or {}).get("drives")
            if not (isinstance(drives, dict) and drives.get("feature") and drives.get("arg")):
                raise NotSupported(f"{f.get('id')}: a parameter change without a `drives` binding names a "
                                   "SOLIDWORKS dimension; the second kernel cannot follow it")
            target = next((g for g in self.spec["features"] if isinstance(g, dict) and g.get("id") == drives["feature"]),
                          None)
            if target is None or drives["arg"] not in (target.get("args") or {}):
                raise SpecError(f"{f.get('id')}: drives an unknown feature or argument")
            target["args"][drives["arg"]] = f["args"].get("value")
            self.log.append(f"{f.get('id')}: {drives['feature']}.{drives['arg']} built as {f['args'].get('value')}")

    def run(self) -> Built:
        for n, f in enumerate(self.spec["features"]):
            if not isinstance(f, dict):
                raise SpecError(f"feature {n + 1} is not an object")
            op = f.get("$op")
            if op not in V1_OPS and self.language < 2:
                raise SpecError(f"{f.get('id')}: {op!r} is not a v1 operation (set \"language\": 2)")
            fn = getattr(self, "op_" + str(op).replace(".", "_"), None)
            if fn is None:
                raise NotSupported(f"{f.get('id')}: {op} is not built by the second kernel yet")
            fn(f, f.get("args") or {})
        if self.unused:
            raise SpecError(f"sketch {self.unused[0]} is never used")
        if not self.bodies:
            raise SpecError("the spec makes no solid")
        shapes = list(self.bodies.values())
        if len(shapes) == 1:
            shape = shapes[0]
        else:
            from OCP.BRep import BRep_Builder
            from OCP.TopoDS import TopoDS_Compound
            comp, bld = TopoDS_Compound(), BRep_Builder()
            bld.MakeCompound(comp)
            for s in shapes:
                bld.Add(comp, s)
            shape = comp
        return Built(shape, dict(self.bodies), self.log, self.tolerance_class)

    # ---- sketches ----
    def _frame(self, f) -> F.Frame:
        name = str(f.get("sketched_on") or "")
        fr = self.frames.get(name.strip().lower()) or self.frames.get(name)
        if fr is None:
            raise SpecError(f"{f.get('id')}: sketched_on {name!r} is not a known plane")
        return fr

    def _add_sketch(self, f, loops, kind):
        if self.unused and self.language < 2:
            raise SpecError(f"{f.get('id')}: sketch {self.unused[-1]} was never used")
        sid = str(f.get("id"))
        if sid in self.sketches:
            raise SpecError(f"{sid}: sketch id already used")
        self.sketches[sid] = Sketch(sid, self._frame(f), loops, kind)
        self.unused.append(sid)
        self.pending = sid

    def _class(self, c: str):
        if CLASS_ORDER.index(c) > CLASS_ORDER.index(self.tolerance_class):
            self.tolerance_class = c

    def _plane(self, name) -> F.Frame:
        fr = self.frames.get(str(name).strip().lower()) or self.frames.get(str(name))
        if fr is None:
            raise SpecError(f"{name!r} is not a known plane")
        return fr

    def op_sketch_rectangle(self, f, a):
        w, h = float(a["width"]), float(a["height"])
        if not (w > 0 and h > 0):
            raise SpecError(f"{f.get('id')}: width and height must be > 0")
        self._add_sketch(f, [F.rectangle_loop(w, h, float(a.get("x", 0)), float(a.get("y", 0)))], "rectangle")

    def op_sketch_circle(self, f, a):
        r = float(a["radius"]) if "radius" in a else float(a["diameter"]) / 2
        if not r > 0:
            raise SpecError(f"{f.get('id')}: radius must be > 0")
        self._add_sketch(f, [[["C", float(a.get("x", 0)), float(a.get("y", 0)), r]]], "circle")

    def op_sketch_loops(self, f, a):
        loops = a.get("loops")
        if not (isinstance(loops, list) and loops and all(isinstance(lp, list) and lp for lp in loops)):
            raise SpecError(f"{f.get('id')}: loops must be a list of segment lists")
        self._add_sketch(f, [[[s[0], *map(float, s[1:])] for s in lp] for lp in loops], "loops")

    def op_sketch_profile(self, f, a):
        pts = a.get("points") or []
        if len(pts) < 3 or any(float(p[1]) < 0 for p in pts):
            raise SpecError(f"{f.get('id')}: a profile needs 3+ points with y >= 0")
        self._add_sketch(f, [F.polygon_loop([(float(p[0]), float(p[1])) for p in pts])], "profile")

    def _take(self, f, wanted: str | None = None) -> Sketch:
        sid = wanted or (self.unused[-1] if self.unused else None)
        if sid is None or sid not in self.unused:
            raise SpecError(f"{f.get('id')}: no unused sketch {sid or ''} to use".rstrip())
        self.unused.remove(sid)
        self.pending = self.unused[-1] if self.unused else None
        return self.sketches[sid]

    def _wire(self, loop, fr: F.Frame):
        mw = BRepBuilderAPI_MakeWire()
        for seg in loop:
            mw.Add(self._edge(seg, fr))
            if not mw.IsDone():
                raise SpecError("a loop is not closed or its segments do not connect")
        wire = mw.Wire()
        if not BRep_Tool.IsClosed_s(wire):
            raise SpecError("a loop is not closed")
        return wire

    # ---- faces and tools ----
    def _edge(self, seg, fr: F.Frame):
        k = seg[0]
        if k == "L":
            a, b = fr.point(seg[1], seg[2]), fr.point(seg[3], seg[4])
            if F.norm(F.sub(a, b)) < LIN_TOL:
                raise SpecError("zero-length line")
            return BRepBuilderAPI_MakeEdge(_p(a), _p(b)).Edge()
        if k == "A":
            cu, cv, su, sv, eu, ev = seg[1:7]
            r = math.hypot(su - cu, sv - cv)
            if abs(math.hypot(eu - cu, ev - cv) - r) > 1e-6 * max(1.0, r):
                raise SpecError("arc end is not on its circle")
            t0, d = math.atan2(sv - cv, su - cu), F.arc_sweep(seg)
            tm = t0 + d / 2
            arc = GC_MakeArcOfCircle(_p(fr.point(su, sv)), _p(fr.point(cu + r * math.cos(tm), cv + r * math.sin(tm))),
                                     _p(fr.point(eu, ev))).Value()
            return BRepBuilderAPI_MakeEdge(arc).Edge()
        if k == "C":
            circ = gp_Circ(gp_Ax2(_p(fr.point(seg[1], seg[2])), _d(fr.n), _d(fr.x)), float(seg[3]))
            return BRepBuilderAPI_MakeEdge(circ).Edge()
        raise SpecError(f"unknown segment kind {k!r}")

    def _face(self, loop, fr: F.Frame):
        mf = BRepBuilderAPI_MakeFace(self._wire(loop, fr), True)
        if not mf.IsDone():
            raise BuildError("no planar face from a loop")
        return mf.Face()

    def _sweep_tool(self, sk: Sketch, make):
        """`make(face)` -> solid for one loop; the largest loop is the outside, the others openings."""
        loops = sorted(sk.loops, key=lambda lp: -abs(F.loop_area(lp)))
        tool = make(self._face(loops[0], sk.frame))
        for lp in loops[1:]:
            tool = _boolean("cut", tool, make(self._face(lp, sk.frame)), "opening")
        return tool

    def _apply(self, f, tool, op: str, body: str = "main"):
        current = self.bodies.get(body)
        if current is None:
            if op == "cut":
                raise SpecError(f"{f.get('id')}: a cut needs a solid to cut")
            self.bodies[body] = _unify(tool)
        else:
            self.bodies[body] = _boolean(op, current, tool, str(f.get("id")))
        self.tools[str(f.get("id"))] = Tool(tool, op, body)

    def _prism(self, sk: Sketch, depth: float, direction: str):
        n = sk.frame.n
        if direction == "normal":
            start, vec = (0.0, 0.0, 0.0), F.mul(n, depth)
        elif direction == "reverse":
            start, vec = (0.0, 0.0, 0.0), F.mul(n, -depth)
        elif direction == "both":
            start, vec = F.mul(n, -depth), F.mul(n, 2 * depth)
        else:
            raise SpecError(f"direction must be normal, reverse or both, not {direction!r}")

        def make(face):
            if start != (0.0, 0.0, 0.0):
                face = _translated(face, start)
            return BRepPrimAPI_MakePrism(face, gp_Vec(*vec)).Shape()
        return self._sweep_tool(sk, make)

    def _through_length(self, sk: Sketch) -> float:
        """Long enough to pass through every body from the sketch plane, in both directions."""
        if not self.bodies:
            raise SpecError("through_all needs a solid")
        reach = 0.0
        for b in self.bodies.values():
            x0, y0, z0, x1, y1, z1 = bbox(b)
            for c in ((x0, y0, z0), (x1, y1, z1), (x0, y1, z1), (x1, y0, z0), (x0, y0, z1), (x1, y1, z0),
                      (x0, y1, z0), (x1, y0, z1)):
                reach = max(reach, F.norm(F.sub(c, sk.frame.origin)))
        return 2 * reach + 10.0

    # ---- features ----
    def op_feature_extrude(self, f, a):
        sk = self._take(f, a.get("sketch"))
        if a.get("through_all"):
            raise NotSupported(f"{f.get('id')}: a through-all boss has no defined end in the language")
        depth = float(a.get("depth", float("nan")))
        if not depth > 0:
            raise SpecError(f"{f.get('id')}: depth must be > 0")
        tool = self._prism(sk, depth, str(a.get("direction", "normal")))
        self._apply(f, tool, "add", str(a.get("body") or a.get("new_body") or "main"))
        self.log.append(f"{f.get('id')}: extrude {sk.id} depth {depth} {a.get('direction', 'normal')}")

    def op_feature_cut(self, f, a):
        sk = self._take(f, a.get("sketch"))
        direction = str(a.get("direction", "both"))      # v1: cuts are symmetric about the sketch plane
        if a.get("through_all"):
            depth = self._through_length(sk)
        else:
            depth = float(a.get("depth", float("nan")))
            if not depth > 0:
                raise SpecError(f"{f.get('id')}: depth must be > 0 unless through_all")
        tool = self._prism(sk, depth, direction)
        self._apply(f, tool, "cut", str(a.get("body") or "main"))
        self.log.append(f"{f.get('id')}: cut {sk.id} {'through all' if a.get('through_all') else depth} {direction}")

    def op_feature_revolve(self, f, a):
        sk = self._take(f, a.get("sketch"))
        angle = float(a.get("angle", 360))
        if not 0 < angle <= 360:
            raise SpecError(f"{f.get('id')}: angle must be in (0, 360]")
        fr = sk.frame
        axis = a.get("axis", "u")
        if axis == "u":
            origin, direction = fr.origin, fr.x
        elif axis == "v":
            origin, direction = fr.origin, fr.y
        elif isinstance(axis, dict) and isinstance(axis.get("line"), list) and len(axis["line"]) == 2:
            (u1, v1), (u2, v2) = axis["line"]
            origin, direction = fr.point(u1, v1), F.unit(F.sub(fr.point(u2, v2), fr.point(u1, v1)), "axis line")
        else:
            raise SpecError(f"{f.get('id')}: axis must be u, v or {{line: [[u1, v1], [u2, v2]]}}")
        self._check_one_side(f, sk, origin, direction)
        rad = math.radians(angle)
        mode = str(a.get("direction", "normal"))
        if mode not in ("normal", "reverse", "both"):
            raise SpecError(f"{f.get('id')}: direction must be normal, reverse or both")

        def make(face):
            if mode == "both" and angle < 360:
                face = _rotated(face, origin, direction, -rad / 2)
            sweep = -rad if mode == "reverse" else rad
            return BRepPrimAPI_MakeRevol(face, gp_Ax1(_p(origin), _d(direction)), sweep, True).Shape()
        tool = self._sweep_tool(sk, make)
        op = "cut" if f.get("$op") == "feature.cut_revolve" else "add"
        self._apply(f, tool, op, str(a.get("body") or a.get("new_body") or "main"))
        self.log.append(f"{f.get('id')}: revolve {sk.id} {angle} deg")

    def _check_one_side(self, f, sk: Sketch, origin, direction):
        """Every point of the profile on one side of the axis (touching allowed), measured in the sketch plane."""
        side = F.cross(sk.frame.n, direction)            # in-plane, perpendicular to the axis
        su, sv = F.dot(sk.frame.x, side), F.dot(sk.frame.y, side)    # that direction in (u, v)
        signs = set()
        for lp in sk.loops:
            for s in lp:
                if s[0] == "L":
                    pts = [(s[1], s[2]), (s[3], s[4])]
                else:
                    c = (s[1], s[2])
                    r = s[3] if s[0] == "C" else math.hypot(s[3] - s[1], s[4] - s[2])
                    pts = [(s[3], s[4]), (s[5], s[6])] if s[0] == "A" else []
                    for sign in (1.0, -1.0):      # the curve's extremes across the axis, when the curve has them
                        th = math.atan2(sign * sv, sign * su)
                        if s[0] == "C" or _on_arc(s, th):
                            pts.append((c[0] + r * math.cos(th), c[1] + r * math.sin(th)))
                for u, v in pts:
                    dist = F.dot(F.sub(sk.frame.point(u, v), origin), side)
                    if abs(dist) > LIN_TOL:
                        signs.add(dist > 0)
        if len(signs) > 1:
            raise SpecError(f"{f.get('id')}: the profile crosses the revolve axis")

    op_feature_cut_revolve = op_feature_revolve

    def op_feature_hole(self, f, a):
        d = float(a.get("diameter", float("nan")))
        if not d > 0:
            raise SpecError(f"{f.get('id')}: diameter must be > 0")
        fr = self._frame({**f, "sketched_on": f.get("sketched_on") or "front"})
        centers = a.get("centers") or [a.get("center") or [0, 0]]
        kind = str(a.get("type") or "simple")
        if kind not in ("simple", "counterbore", "countersink"):
            raise SpecError(f"{f.get('id')}: hole type must be simple, counterbore or countersink")
        sk = Sketch(str(f.get("id")), fr, [[["C", float(c[0]), float(c[1]), d / 2]] for c in centers], "holes")
        depth = self._through_length(sk) if a.get("through_all") else float(a.get("depth", float("nan")))
        if not depth > 0:
            raise SpecError(f"{f.get('id')}: depth must be > 0 unless through_all")
        direction = str(a.get("direction", "both"))
        tool = None
        for lp in sk.loops:   # each centre is its own bore (not an opening of a larger loop)
            if kind == "simple":
                one = self._prism(Sketch(sk.id, fr, [lp], "hole"), depth, direction)
            else:
                one = self._stepped_hole(f, a, fr.point(lp[0][1], lp[0][2]), fr.n, d, depth, direction, kind)
            tool = one if tool is None else _boolean("add", tool, one, str(f.get("id")))
        self._apply(f, tool, "cut", str(a.get("body") or "main"))
        self.log.append(f"{f.get('id')}: hole dia {d} x {len(centers)}")

    def _stepped_hole(self, f, a, p, n, d, depth, direction, kind):
        if direction not in ("normal", "reverse"):
            raise SpecError(f"{f.get('id')}: a {kind} hole needs direction normal or reverse (its entry side)")
        ax = gp_Ax2(_p(p), _d(n if direction == "normal" else F.mul(n, -1)))
        tool = BRepPrimAPI_MakeCylinder(ax, d / 2, depth).Shape()
        if kind == "counterbore":
            cd, ch = float(a.get("cbore_diameter", float("nan"))), float(a.get("cbore_depth", float("nan")))
            if not (cd > d and 0 < ch < depth):
                raise SpecError(f"{f.get('id')}: cbore_diameter must exceed diameter, 0 < cbore_depth < depth")
            return _boolean("add", tool, BRepPrimAPI_MakeCylinder(ax, cd / 2, ch).Shape(), str(f.get("id")))
        cd, ang = float(a.get("csk_diameter", float("nan"))), float(a.get("csk_angle", 90))
        if not (cd > d and 0 < ang < 180):
            raise SpecError(f"{f.get('id')}: csk_diameter must exceed diameter, 0 < csk_angle < 180")
        h = (cd - d) / 2 / math.tan(math.radians(ang) / 2)
        if h >= depth:
            raise SpecError(f"{f.get('id')}: the countersink is deeper than the hole")
        return _boolean("add", tool, BRepPrimAPI_MakeCone(ax, cd / 2, d / 2, h).Shape(), str(f.get("id")))

    # ---- edge treatments ----
    def _select_edges(self, f, a, body):
        sel = a.get("select", "corners")
        if a.get("edges"):
            raise NotSupported(f"{f.get('id')}: edge indices are SOLIDWORKS-specific; use select")
        if sel == "corners":
            picks = []
            for e in edges_of(body):
                if BRepAdaptor_Curve(e).GetType() != GeomAbs_Line:
                    continue
                p, q = _edge_points(e)
                if abs(p[0] - q[0]) < LIN_TOL and abs(p[1] - q[1]) < LIN_TOL:
                    picks.append(e)
            return picks
        if sel == "end_circles":
            circles = []
            for e in edges_of(body):
                c = BRepAdaptor_Curve(e)
                if c.GetType() == GeomAbs_Circle:
                    circ = c.Circle()
                    loc, ax = circ.Location(), circ.Axis().Direction()
                    circles.append((e, (loc.X(), loc.Y(), loc.Z()), (ax.X(), ax.Y(), ax.Z()), circ.Radius()))
            if not circles:
                raise BuildError(f"{f.get('id')}: no circular edges - not a turned part")
            ax = circles[0][2]
            if any(abs(abs(F.dot(c[2], ax)) - 1) > 1e-9 for c in circles):
                raise BuildError(f"{f.get('id')}: circular edges do not share one axis - not a turned part")
            pos = [F.dot(c[1], ax) for c in circles]
            picks = []
            for end in (min(pos), max(pos)):
                plane = [c for c, p in zip(circles, pos) if abs(p - end) < LIN_TOL]
                rmax = max(c[3] for c in plane)
                picks += [c[0] for c in plane if abs(c[3] - rmax) < LIN_TOL]
            return picks
        if isinstance(sel, dict):
            if self.language < 2 or set(sel) != {"edges"}:
                raise SpecError(f"{f.get('id')}: a query select is {{\"edges\": {{...}}}} in a language 2 spec")
            self._class("B")
            try:
                return Q.select_edges(body, sel["edges"])
            except Q.QueryCountError as ex:
                raise QueryMismatch(f"{f.get('id')}: {ex}") from None
            except ValueError as ex:
                raise SpecError(f"{f.get('id')}: {ex}") from None
        raise SpecError(f"{f.get('id')}: select must be corners or end_circles")

    def op_feature_fillet(self, f, a):
        body = str(a.get("body") or "main")
        shape = self.bodies.get(body)
        r = float(a.get("radius", float("nan")))
        if shape is None or not r > 0:
            raise SpecError(f"{f.get('id')}: a fillet needs a solid and radius > 0")
        picks = self._select_edges(f, a, shape)
        mk = BRepFilletAPI_MakeFillet(shape)
        for e in picks:
            mk.Add(r, e)
        mk.Build()
        if not mk.IsDone():
            raise BuildError(f"{f.get('id')}: OpenCascade could not fillet {len(picks)} edge(s) at r={r}")
        self.bodies[body] = _unify(mk.Shape())
        self.log.append(f"{f.get('id')}: fillet r={r} on {len(picks)} edge(s)")

    def op_feature_chamfer(self, f, a):
        body = str(a.get("body") or "main")
        shape = self.bodies.get(body)
        c = float(a.get("distance", float("nan")))
        if shape is None or not c > 0:
            raise SpecError(f"{f.get('id')}: a chamfer needs a solid and distance > 0")
        if a.get("angle") is not None and float(a["angle"]) != 45:
            raise NotSupported(f"{f.get('id')}: angle-distance chamfers come with M1")
        picks = self._select_edges(f, a, shape)
        mk = BRepFilletAPI_MakeChamfer(shape)
        for e in picks:
            mk.Add(c, e)
        mk.Build()
        if not mk.IsDone():
            raise BuildError(f"{f.get('id')}: OpenCascade could not chamfer {len(picks)} edge(s) at {c}")
        self.bodies[body] = _unify(mk.Shape())
        self.log.append(f"{f.get('id')}: chamfer {c} on {len(picks)} edge(s)")

    # ---- patterns ----
    def _seeds(self, f, a) -> list[Tool]:
        names = a.get("features") or [a.get("feature")]
        out = []
        for seed in names:
            if seed not in self.tools:
                raise SpecError(f"{f.get('id')}: feature {seed!r} is not an earlier solid feature")
            out.append(self.tools[seed])
        return out

    def _seed(self, f, a) -> Tool:
        return self._seeds(f, a)[0]

    def op_feature_pattern_linear(self, f, a):
        seeds = self._seeds(f, a)
        count, spacing = int(a.get("count", 2)), float(a.get("spacing", float("nan")))
        if not (2 <= count <= 1000 and spacing > 0):
            raise SpecError(f"{f.get('id')}: count 2..1000 and spacing > 0 needed")
        d = F.axis(a.get("direction", "X"))
        if a.get("reversed"):
            d = F.mul(d, -1)
        steps = [F.mul(d, k * spacing) for k in range(count)]
        if a.get("count2") or a.get("direction2") or a.get("spacing2"):
            c2, s2 = int(a.get("count2", 2)), float(a.get("spacing2", float("nan")))
            if not (2 <= c2 <= 1000 and s2 > 0):
                raise SpecError(f"{f.get('id')}: count2 2..1000 and spacing2 > 0 needed")
            d2 = F.axis(a.get("direction2", "Y"))
            steps = [F.add(s, F.mul(d2, k * s2)) for s in steps for k in range(c2)]
        for t in seeds:
            for off in steps[1:]:
                inst = _translated(t.shape, off)
                self.bodies[t.body] = _boolean(t.op, self.bodies[t.body], inst, str(f.get("id")))
        self.tools[str(f.get("id"))] = Tool(seeds[0].shape, seeds[0].op, seeds[0].body)
        self.log.append(f"{f.get('id')}: {len(steps)} instance(s) along {d}")

    def op_feature_pattern_circular(self, f, a):
        seeds = self._seeds(f, a)
        t = seeds[0]
        count, angle = int(a.get("count", 2)), float(a.get("angle", 360))
        if not (2 <= count <= 1000 and 0 < angle <= 360):
            raise SpecError(f"{f.get('id')}: count 2..1000 and angle in (0, 360] needed")
        ax = a.get("axis", "Z")
        if isinstance(ax, dict):
            origin, direction = ax.get("origin"), F.unit(ax.get("direction"), "axis direction")
            origin = (float(origin[0]), float(origin[1]), float(origin[2]))
        else:
            key = str(ax).upper()
            center = a.get("center") or [0, 0]
            cx, cy = float(center[0]), float(center[1])
            if key == "Z":
                origin, direction = (cx, cy, 0.0), (0.0, 0.0, 1.0)
            elif key in ("X", "Y") and abs(cx) < 1e-12 and abs(cy) < 1e-12:
                origin, direction = (0.0, 0.0, 0.0), F.AXES[key]
            else:
                raise NotSupported(f"{f.get('id')}: a v1 circular pattern about {key} off the origin is not "
                                   "defined the same way in SOLIDWORKS; use an explicit axis")
        if a.get("equal_spacing") is False:
            raise NotSupported(f"{f.get('id')}: unequal spacing")
        step = angle / count if abs(angle - 360) < 1e-9 else angle / (count - 1)
        if a.get("reversed"):
            step = -step
        for t in seeds:
            for k in range(1, count):
                inst = _rotated(t.shape, origin, direction, math.radians(k * step))
                self.bodies[t.body] = _boolean(t.op, self.bodies[t.body], inst, str(f.get("id")))
        self.tools[str(f.get("id"))] = Tool(t.shape, t.op, t.body)
        self.log.append(f"{f.get('id')}: {count} around {direction} at {origin}, step {step} deg")

    # ---- v2: planes, bodies, mirror, shell, sweep, loft ----
    def op_ref_plane(self, f, a):
        fid = str(f.get("id"))
        if not fid or fid in self.frames:
            raise SpecError(f"{fid!r}: a plane needs a new id")
        try:
            self.frames[fid] = F.plane_frame(a, self.frames)
        except ValueError as ex:
            raise SpecError(f"{fid}: {ex}") from None
        fr = self.frames[fid]
        self.log.append(f"{fid}: plane at {tuple(round(c, 6) for c in fr.origin)} normal {tuple(round(c, 9) for c in fr.n)}")

    def op_body_boolean(self, f, a):
        kind, target, tools = a.get("op"), a.get("target", "main"), a.get("tools")
        if kind not in ("add", "subtract", "intersect") or not (isinstance(tools, list) and tools):
            raise SpecError(f"{f.get('id')}: op add|subtract|intersect and a list of tool bodies needed")
        if target not in self.bodies or any(t not in self.bodies or t == target for t in tools):
            raise SpecError(f"{f.get('id')}: unknown body among {target!r}, {tools!r}")
        shape = self.bodies[target]
        for t in tools:
            shape = _boolean(kind, shape, self.bodies.pop(t), str(f.get("id")))
        self.bodies[target] = shape
        self.log.append(f"{f.get('id')}: {kind} {tools} -> {target}")

    def op_feature_mirror(self, f, a):
        fr = self._plane(a.get("plane", "right"))
        t = gp_Trsf()
        t.SetMirror(gp_Ax2(_p(fr.origin), _d(fr.n)))
        if a.get("body"):
            b = "main" if a["body"] is True else str(a["body"])
            if b not in self.bodies:
                raise SpecError(f"{f.get('id')}: no body {b!r} to mirror")
            image = BRepBuilderAPI_Transform(self.bodies[b], t, True).Shape()
            self.bodies[b] = _boolean("add", self.bodies[b], image, str(f.get("id")))
            self.log.append(f"{f.get('id')}: body {b} joined with its mirror image")
            return
        names = a.get("features")
        if not (isinstance(names, list) and names):
            raise SpecError(f"{f.get('id')}: mirror needs features [ids] or body: true")
        for name in names:
            if name not in self.tools:
                raise SpecError(f"{f.get('id')}: feature {name!r} is not an earlier solid feature")
            tool = self.tools[name]
            image = BRepBuilderAPI_Transform(tool.shape, t, True).Shape()
            self.bodies[tool.body] = _boolean(tool.op, self.bodies[tool.body], image, str(f.get("id")))
        self.log.append(f"{f.get('id')}: mirrored {names}")

    def op_feature_shell(self, f, a):
        b = str(a.get("body") or "main")
        body, t = self.bodies.get(b), float(a.get("thickness", float("nan")))
        if body is None or not t > 0:
            raise SpecError(f"{f.get('id')}: shell needs a solid and thickness > 0")
        rem = a.get("remove")
        if not (isinstance(rem, dict) and set(rem) == {"faces"}):
            raise NotSupported(f"{f.get('id')}: a closed hollow (no face removed) is not built here yet")
        try:
            faces = Q.select_faces(body, rem["faces"])
        except Q.QueryCountError as ex:
            raise QueryMismatch(f"{f.get('id')}: {ex}") from None
        except ValueError as ex:
            raise SpecError(f"{f.get('id')}: {ex}") from None
        lst = List_TopoDS_Shape()
        for face in faces:
            lst.Append(face)
        mk = BRepOffsetAPI_MakeThickSolid()
        mk.MakeThickSolidByJoin(body, lst, -t, 1e-6, BRepOffset_Skin, False, False, GeomAbs_Intersection)
        mk.Build()
        if not mk.IsDone():
            raise BuildError(f"{f.get('id')}: OpenCascade could not shell at {t}")
        self.bodies[b] = _unify(mk.Shape())
        self._class("B")
        self.log.append(f"{f.get('id')}: shell {t}, {len(faces)} face(s) open")

    def _path(self, f, segs):
        """A connected wire of 3D lines and arcs-through-three-points; returns (wire, start point, start tangent)."""
        if not (isinstance(segs, list) and segs):
            raise SpecError(f"{f.get('id')}: path segments needed")
        mw, prev, start, tangent = BRepBuilderAPI_MakeWire(), None, None, None
        for s in segs:
            pts = [tuple(float(c) for c in p) for p in s[1:]]
            if s[0] == "L" and len(pts) == 2:
                e = BRepBuilderAPI_MakeEdge(_p(pts[0]), _p(pts[1])).Edge()
                tan = F.sub(pts[1], pts[0])
            elif s[0] == "A" and len(pts) == 3:
                curve = GC_MakeArcOfCircle(_p(pts[0]), _p(pts[1]), _p(pts[2])).Value()
                e = BRepBuilderAPI_MakeEdge(curve).Edge()
                p0, v0 = gp_Pnt(), gp_Vec()
                curve.D1(curve.FirstParameter(), p0, v0)
                tan = (v0.X(), v0.Y(), v0.Z())
            else:
                raise SpecError(f"{f.get('id')}: path segment must be [\"L\", p1, p2] or [\"A\", p1, pm, p2]")
            if prev is not None and F.norm(F.sub(prev, pts[0])) > LIN_TOL:
                raise SpecError(f"{f.get('id')}: path segments do not connect")
            if start is None:
                start, tangent = pts[0], F.unit(tan, "path tangent")
            prev = pts[-1]
            mw.Add(e)
            if not mw.IsDone():
                raise SpecError(f"{f.get('id')}: path segments do not connect")
        return mw.Wire(), start, tangent

    def _helix(self, f, h):
        try:
            o = tuple(float(c) for c in h["origin"])
            ax, sd = F.unit(h["axis"], "helix axis"), F.unit(h["start_dir"], "helix start_dir")
            r, p, turns = float(h["radius"]), float(h["pitch"]), float(h["turns"])
        except (KeyError, TypeError, ValueError) as ex:
            raise SpecError(f"{f.get('id')}: helix needs origin, axis, radius, pitch, turns, start_dir ({ex})") from None
        if abs(F.dot(ax, sd)) > 1e-9 or not (r > 0 and p > 0 and turns > 0):
            raise SpecError(f"{f.get('id')}: start_dir must be perpendicular to the axis; radius, pitch, turns > 0")
        surf = Geom_CylindricalSurface(gp_Ax3(_p(o), _d(ax), _d(sd)), r)
        line = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(2 * math.pi, p))
        length = turns * math.hypot(2 * math.pi, p)
        edge = BRepBuilderAPI_MakeEdge(line, surf, 0.0, length).Edge()
        BRepLib.BuildCurves3d_s(edge)
        wire = BRepBuilderAPI_MakeWire(edge).Wire()
        start = F.add(o, F.mul(sd, r))
        tangent = F.unit(F.add(F.mul(F.cross(ax, sd), 2 * math.pi * r), F.mul(ax, p)), "helix tangent")
        return wire, start, tangent

    def op_feature_sweep(self, f, a):
        sk = self._take(f, a.get("profile"))
        path = a.get("path") or {}
        helix = isinstance(path, dict) and "helix" in path
        if helix:
            wire, start, tangent = self._helix(f, path["helix"])
            self._class("F")
        elif isinstance(path, dict) and "segments" in path:
            wire, start, tangent = self._path(f, path["segments"])
        else:
            raise SpecError(f"{f.get('id')}: path must be {{segments: [...]}} or {{helix: {{...}}}}")
        fr = sk.frame
        if abs(F.dot(F.sub(start, fr.origin), fr.n)) > LIN_TOL or abs(abs(F.dot(fr.n, tangent)) - 1) > 1e-9:
            raise SpecError(f"{f.get('id')}: the profile plane must contain the path's start point and be "
                            "perpendicular to its start tangent")

        def make(face):
            if not helix:
                return BRepOffsetAPI_MakePipe(wire, face).Shape()
            ps = BRepOffsetAPI_MakePipeShell(wire)
            ps.SetMode(True)                              # Frenet: the profile turns with the helix
            ps.Add(BRepTools.OuterWire_s(face), False, False)
            ps.Build()
            if not ps.IsDone() or not ps.MakeSolid():
                raise BuildError(f"{f.get('id')}: OpenCascade could not sweep along the helix")
            return ps.Shape()
        tool = self._sweep_tool(sk, make)
        op = str(a.get("operation", "add"))
        if op not in ("add", "cut"):
            raise SpecError(f"{f.get('id')}: operation must be add or cut")
        self._apply(f, tool, op, str(a.get("body") or a.get("new_body") or "main"))
        self.log.append(f"{f.get('id')}: sweep {sk.id} along {'a helix' if helix else 'lines/arcs'}")

    def op_feature_loft(self, f, a):
        ids = a.get("sections")
        if not (isinstance(ids, list) and len(ids) >= 2):
            raise SpecError(f"{f.get('id')}: a loft needs two or more sections")
        secs = [self._take(f, sid) for sid in ids]
        if any(len(s.loops) != 1 for s in secs):
            raise SpecError(f"{f.get('id')}: each loft section is one closed loop")
        loops = [s.loops[0] for s in secs]
        circles = [len(lp) == 1 and lp[0][0] == "C" for lp in loops]
        polys = [all(seg[0] == "L" for seg in lp) for lp in loops]
        if not (all(circles) or (all(polys) and len({len(lp) for lp in loops}) == 1)):
            raise SpecError(f"{f.get('id')}: sections must all be single circles, or polygons with the same "
                            "number of segments")
        ts = BRepOffsetAPI_ThruSections(True, True, 1e-6)
        ts.CheckCompatibility(False)
        for s, lp in zip(secs, loops):
            ts.AddWire(self._wire(lp, s.frame))
        ts.Build()
        if not ts.IsDone():
            raise BuildError(f"{f.get('id')}: OpenCascade could not loft the sections")
        op = str(a.get("operation", "add"))
        if op not in ("add", "cut"):
            raise SpecError(f"{f.get('id')}: operation must be add or cut")
        self._apply(f, ts.Shape(), op, str(a.get("body") or a.get("new_body") or "main"))
        self.log.append(f"{f.get('id')}: ruled loft through {ids}")

    # ---- records ----
    def op_feature_parameter(self, f, a):
        pass     # applied up front (_apply_drives)

    def op_doc_note(self, f, a):
        pass


def build(spec: dict) -> Built:
    """Build a spec; raises SpecError (invalid), NotSupported (not built here yet) or BuildError (kernel failed)."""
    try:
        return Builder(spec).run()
    except KeyError as ex:
        raise SpecError(f"malformed spec: missing {ex}") from None
    except (TypeError, ValueError) as ex:
        if isinstance(ex, (SpecError,)):
            raise
        # a wrong type in the spec (float(None), a list where a number belongs) or an internal error: either way
        # nothing was verified; the message says which call failed
        raise BuildError(f"could not build: {type(ex).__name__}: {ex}") from None
