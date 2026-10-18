"""Edge and face selection by geometric query (Part Language v2, section 4), evaluated on OpenCascade's B-rep.

Every field given must hold; `expect_count` is required and must match exactly - a different count stops the step
(QueryCountError), because selecting "about the right" edges is how fillets land on the wrong ones. Seam edges of
periodic faces and degenerated edges are not edges of the part as a designer sees it and are never selected.
"""
from __future__ import annotations

import math

from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.collections import IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as _AncestorMap
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as _ShapeMap
from OCP.gp import gp_Pnt, gp_Vec

from . import frames as F

TOL = 1e-6
EDGE_KEYS = {"kind", "parallel_to", "at", "at_extreme", "radius", "within_box", "convex", "expect_count"}
FACE_KEYS = {"kind", "parallel_to", "normal", "at", "at_extreme", "radius", "within_box", "expect_count"}


class QueryCountError(RuntimeError):
    """The query found a different number of entities than the spec expects."""


def _box(shape) -> list[float]:
    b = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, b, False, False)
    lo, hi = b.CornerMin(), b.CornerMax()
    return [lo.X(), lo.Y(), lo.Z(), hi.X(), hi.Y(), hi.Z()]


def _axis_index(name) -> int:
    key = str(name).strip().upper().lstrip("+-")
    if key not in ("X", "Y", "Z"):
        raise ValueError(f"axis must be X, Y or Z, not {name!r}")
    return "XYZ".index(key)


def _parallel(a, b) -> bool:
    return abs(abs(F.dot(F.unit(a), F.unit(b))) - 1) <= 1e-9


def _common(q: dict, ent_box: list[float], body_box: list[float]) -> bool:
    if "at" in q:
        i, v = _axis_index(q["at"]["axis"]), float(q["at"]["value"])
        if abs(ent_box[i] - v) > TOL or abs(ent_box[i + 3] - v) > TOL:
            return False
    if "at_extreme" in q:
        i, which = _axis_index(q["at_extreme"]["axis"]), str(q["at_extreme"]["which"]).lower()
        if which not in ("max", "min"):
            raise ValueError("at_extreme.which must be max or min")
        v = body_box[i + 3] if which == "max" else body_box[i]
        if abs(ent_box[i] - v) > TOL or abs(ent_box[i + 3] - v) > TOL:
            return False
    if "within_box" in q:
        wb = [float(x) for x in q["within_box"]]
        if len(wb) != 6:
            raise ValueError("within_box must be [xmin, ymin, zmin, xmax, ymax, zmax]")
        if any(ent_box[k] < wb[k] - TOL for k in range(3)) or any(ent_box[k + 3] > wb[k + 3] + TOL for k in range(3)):
            return False
    return True


def _check_keys(q: dict, allowed: set, what: str):
    if not isinstance(q, dict):
        raise ValueError(f"{what} query must be an object")
    extra = set(q) - allowed
    if extra:
        raise ValueError(f"{what} query: unknown fields {sorted(extra)}")
    n = q.get("expect_count")
    if not (isinstance(n, int) and not isinstance(n, bool) and n >= 1):
        raise ValueError(f"{what} query needs expect_count (a whole number >= 1)")


def outward_normal(face, p) -> tuple[float, float, float]:
    surf = BRep_Tool.Surface_s(face)
    proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(*p), surf)
    u, v = proj.LowerDistanceParameters()
    pt, du, dv = gp_Pnt(), gp_Vec(), gp_Vec()
    BRepAdaptor_Surface(face).D1(u, v, pt, du, dv)
    n = du.Crossed(dv)
    if n.Magnitude() < 1e-14:
        raise ValueError("degenerate surface normal")
    n.Normalize()
    s = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0
    return (s * n.X(), s * n.Y(), s * n.Z())


def _into_face(face, edge, p, t):
    """The unit direction in `face`, perpendicular to `edge` at p, pointing into the face. A face's boundary is
    oriented so that its material lies on the left: into-face = outward normal x the edge's tangent as the face
    traverses it."""
    ex = TopExp_Explorer(face, TopAbs_EDGE)
    while ex.More():
        e2 = ex.Current()
        if e2.IsSame(edge):
            tt = F.mul(t, -1) if e2.Orientation() == TopAbs_REVERSED else t
            return F.unit(F.cross(outward_normal(face, p), tt))
        ex.Next()
    return None


def _convex(body, edge, faces, size: float):
    """True for a convex edge, False for a concave one, None where the faces meet tangentially: convex when the
    second face leaves the edge on the material side of the first face's tangent plane."""
    if len(faces) != 2:
        return None
    c = BRepAdaptor_Curve(edge)
    pnt, vec = gp_Pnt(), gp_Vec()
    c.D1((c.FirstParameter() + c.LastParameter()) / 2, pnt, vec)
    p, t = (pnt.X(), pnt.Y(), pnt.Z()), F.unit((vec.X(), vec.Y(), vec.Z()), "edge tangent")
    d2 = _into_face(faces[1], edge, p, t)
    if d2 is None:
        return None
    s = F.dot(outward_normal(faces[0], p), d2)
    if abs(s) < 1e-6:
        return None
    return s < 0


def select_edges(body, q: dict) -> list:
    _check_keys(q, EDGE_KEYS, "edge")
    kind = str(q.get("kind", "any")).lower()
    if kind not in ("line", "circle", "any"):
        raise ValueError("edge kind must be line, circle or any")
    body_box = _box(body)
    size = max(body_box[3] - body_box[0], body_box[4] - body_box[1], body_box[5] - body_box[2])
    anc = _AncestorMap()
    TopExp.MapShapesAndAncestors_s(body, TopAbs_EDGE, TopAbs_FACE, anc)
    out = []
    for i in range(1, anc.Extent() + 1):
        e = TopoDS.Edge(anc.FindKey(i))
        faces = []
        it = anc.FindFromIndex(i)
        for s in it:
            f = TopoDS.Face(s)
            if not any(f.IsSame(g) for g in faces):
                faces.append(f)
        if BRep_Tool.Degenerated_s(e) or any(BRep_Tool.IsClosed_s(e, f) for f in faces):
            continue                                    # degenerated, or the seam of a periodic face
        c = BRepAdaptor_Curve(e)
        t = c.GetType()
        if kind == "line" and t != GeomAbs_Line or kind == "circle" and t != GeomAbs_Circle:
            continue
        if "parallel_to" in q:
            want = F.axis(q["parallel_to"], "parallel_to")
            if t == GeomAbs_Line:
                d = c.Line().Direction()
            elif t == GeomAbs_Circle:
                d = c.Circle().Axis().Direction()
            else:
                continue
            if not _parallel((d.X(), d.Y(), d.Z()), want):
                continue
        if "radius" in q:
            if t != GeomAbs_Circle or abs(c.Circle().Radius() - float(q["radius"])) > TOL:
                continue
        if not _common(q, _box(e), body_box):
            continue
        if "convex" in q:
            cv = _convex(body, e, faces, size)
            if cv is None or cv != bool(q["convex"]):
                continue
        out.append(e)
    if len(out) != q["expect_count"]:
        raise QueryCountError(f"edge query {q} found {len(out)} edge(s), the spec expects {q['expect_count']}")
    return out


def select_faces(body, q: dict) -> list:
    _check_keys(q, FACE_KEYS, "face")
    kind = str(q.get("kind", "any")).lower()
    if kind not in ("plane", "cylinder", "any"):
        raise ValueError("face kind must be plane, cylinder or any")
    body_box = _box(body)
    m = _ShapeMap()
    TopExp.MapShapes_s(body, TopAbs_FACE, m)
    out = []
    for i in range(1, m.Extent() + 1):
        f = TopoDS.Face(m.FindKey(i))
        s = BRepAdaptor_Surface(f)
        t = s.GetType()
        if kind == "plane" and t != GeomAbs_Plane or kind == "cylinder" and t != GeomAbs_Cylinder:
            continue
        if "normal" in q:
            if t != GeomAbs_Plane:
                continue
            d = s.Plane().Axis().Direction()
            n = (d.X(), d.Y(), d.Z())
            if f.Orientation() == TopAbs_REVERSED:
                n = F.mul(n, -1)
            if F.dot(n, F.axis(q["normal"], "normal")) < 1 - 1e-9:
                continue
        if "parallel_to" in q:
            if t != GeomAbs_Cylinder:
                continue
            d = s.Cylinder().Axis().Direction()
            if not _parallel((d.X(), d.Y(), d.Z()), F.axis(q["parallel_to"], "parallel_to")):
                continue
        if "radius" in q:
            if t != GeomAbs_Cylinder or abs(s.Cylinder().Radius() - float(q["radius"])) > TOL:
                continue
        if not _common(q, _box(f), body_box):
            continue
        out.append(f)
    if len(out) != q["expect_count"]:
        raise QueryCountError(f"face query {q} found {len(out)} face(s), the spec expects {q['expect_count']}")
    return out
