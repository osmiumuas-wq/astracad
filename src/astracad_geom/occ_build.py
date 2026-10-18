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
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire,
                                BRepBuilderAPI_Transform)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.Bnd import Bnd_Box
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

from . import frames as F

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
    op = BRepAlgoAPI_Fuse(a, b) if kind == "add" else BRepAlgoAPI_Cut(a, b)
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
        if self.pending is not None:
            raise SpecError(f"sketch {self.pending} is never used")
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
        if self.pending is not None and self.language < 2:
            raise SpecError(f"{f.get('id')}: sketch {self.pending} was never used")
        sid = str(f.get("id"))
        self.sketches[sid] = Sketch(sid, self._frame(f), loops, kind)
        self.pending = sid

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
        sid = wanted or self.pending
        if sid is None or sid not in self.sketches:
            raise SpecError(f"{f.get('id')}: no sketch to use")
        if sid == self.pending:
            self.pending = None
        return self.sketches.pop(sid)

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
        mw = BRepBuilderAPI_MakeWire()
        for seg in loop:
            mw.Add(self._edge(seg, fr))
            if not mw.IsDone():
                raise SpecError("a loop is not closed or its segments do not connect")
        wire = mw.Wire()
        if not BRep_Tool.IsClosed_s(wire):
            raise SpecError("a loop is not closed")
        mf = BRepBuilderAPI_MakeFace(wire, True)
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
        if (a.get("type") or "simple") != "simple":
            raise NotSupported(f"{f.get('id')}: counterbore/countersink holes come with M1")
        sk = Sketch(str(f.get("id")), fr, [[["C", float(c[0]), float(c[1]), d / 2]] for c in centers], "holes")
        depth = self._through_length(sk) if a.get("through_all") else float(a.get("depth", float("nan")))
        if not depth > 0:
            raise SpecError(f"{f.get('id')}: depth must be > 0 unless through_all")
        direction = str(a.get("direction", "both"))
        tool = None
        for lp in sk.loops:   # each centre is its own bore (not an opening of a larger loop)
            one = self._prism(Sketch(sk.id, fr, [lp], "hole"), depth, direction)
            tool = one if tool is None else _boolean("add", tool, one, str(f.get("id")))
        self._apply(f, tool, "cut", str(a.get("body") or "main"))
        self.log.append(f"{f.get('id')}: hole dia {d} x {len(centers)}")

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
            raise NotSupported(f"{f.get('id')}: query selection comes with M1")
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
    def _seed(self, f, a) -> Tool:
        seed = a.get("feature")
        if seed not in self.tools:
            raise SpecError(f"{f.get('id')}: feature {seed!r} is not an earlier solid feature")
        return self.tools[seed]

    def op_feature_pattern_linear(self, f, a):
        t = self._seed(f, a)
        count, spacing = int(a.get("count", 2)), float(a.get("spacing", float("nan")))
        if not (2 <= count <= 1000 and spacing > 0):
            raise SpecError(f"{f.get('id')}: count 2..1000 and spacing > 0 needed")
        d = F.axis(a.get("direction", "X"))
        if a.get("reversed"):
            d = F.mul(d, -1)
        if a.get("count2") or a.get("direction2"):
            raise NotSupported(f"{f.get('id')}: two-direction patterns come with M1")
        for k in range(1, count):
            inst = _translated(t.shape, F.mul(d, k * spacing))
            self.bodies[t.body] = _boolean(t.op, self.bodies[t.body], inst, str(f.get("id")))
        self.tools[str(f.get("id"))] = Tool(t.shape, t.op, t.body)
        self.log.append(f"{f.get('id')}: {count} x {spacing} along {d}")

    def op_feature_pattern_circular(self, f, a):
        t = self._seed(f, a)
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
        for k in range(1, count):
            inst = _rotated(t.shape, origin, direction, math.radians(k * step))
            self.bodies[t.body] = _boolean(t.op, self.bodies[t.body], inst, str(f.get("id")))
        self.tools[str(f.get("id"))] = Tool(t.shape, t.op, t.body)
        self.log.append(f"{f.get('id')}: {count} around {direction} at {origin}, step {step} deg")

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
