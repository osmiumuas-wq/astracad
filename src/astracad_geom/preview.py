"""A shaded isometric picture of an OpenCascade shape, as SVG - drawn on this PC from the spec, before anything is
built in SOLIDWORKS.

Faces: the shape is meshed, each triangle facing the viewer is shaded by its normal, and triangles are painted far to
near. Edges: every B-rep edge is sampled and drawn only where a depth buffer of those triangles says it is in front -
so hidden edges stay hidden. Numbers only go into the SVG (no text from the spec).
"""
from __future__ import annotations

import math

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GeomAbs import GeomAbs_Line
from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS

from . import frames as F
from .occ_build import bbox, edges_of

BASE = (122, 153, 194)          # steel blue
EDGE = "#1d2939"


def _triangles(shape, lin: float, ang: float):
    BRepMesh_IncrementalMesh(shape, lin, False, ang, True)
    out = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            pts = []
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i) if loc.IsIdentity() else tri.Node(i).Transformed(trsf)
                pts.append((p.X(), p.Y(), p.Z()))
            flip = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                if flip:
                    b, c = c, b
                out.append((pts[a - 1], pts[b - 1], pts[c - 1]))
        ex.Next()
    return out


def _edge_points(e, lin: float):
    c = BRepAdaptor_Curve(e)
    t0, t1 = c.FirstParameter(), c.LastParameter()
    if c.GetType() == GeomAbs_Line:
        n = 2
    else:
        length_guess = max(1e-9, abs(t1 - t0)) * 50
        n = int(min(160, max(16, length_guess / max(lin, 1e-6))))
    pts = []
    for k in range(n):
        p = c.Value(t0 + (t1 - t0) * k / (n - 1))
        pts.append((p.X(), p.Y(), p.Z()))
    return pts


def render(shape, width: int = 560, height: int = 400, eye=(1.0, 1.0, 1.0)) -> dict:
    """{svg, triangles, edges}: the picture and what went into it."""
    box = bbox(shape)
    diag = max(1e-6, math.dist(box[:3], box[3:]))
    tris = _triangles(shape, max(diag * 0.0015, 0.005), 0.25)
    e = F.unit(eye)
    right = F.unit(F.cross((0.0, 1.0, 0.0), e)) if abs(F.dot(e, (0.0, 1.0, 0.0))) < 0.99 else (1.0, 0.0, 0.0)
    up = F.cross(e, right)
    light = F.unit(F.add(F.add(F.mul(e, 0.7), F.mul(up, 0.6)), F.mul(right, -0.25)))

    def proj(p):
        return F.dot(p, right), F.dot(p, up), F.dot(p, e)

    corners = [proj((x, y, z)) for x in (box[0], box[3]) for y in (box[1], box[4]) for z in (box[2], box[5])]
    x0, x1 = min(c[0] for c in corners), max(c[0] for c in corners)
    y0, y1 = min(c[1] for c in corners), max(c[1] for c in corners)
    margin = 18
    s = min((width - 2 * margin) / max(x1 - x0, 1e-9), (height - 2 * margin) / max(y1 - y0, 1e-9))
    ox = (width - (x1 - x0) * s) / 2
    oy = (height - (y1 - y0) * s) / 2

    def screen(p):
        u, v, d = proj(p)
        return ox + (u - x0) * s, height - (oy + (v - y0) * s), d

    faces = []
    for a, b, c in tris:
        nrm = F.cross(F.sub(b, a), F.sub(c, a))
        ln = F.norm(nrm)
        if ln < 1e-14:
            continue
        nrm = F.mul(nrm, 1 / ln)
        if F.dot(nrm, e) <= 1e-9:
            continue                         # facing away from the viewer
        k = 0.42 + 0.58 * max(0.0, F.dot(nrm, light))
        sa, sb, sc = screen(a), screen(b), screen(c)
        faces.append(((sa[2] + sb[2] + sc[2]) / 3, (sa, sb, sc), k))
    faces.sort(key=lambda f: f[0])

    # depth buffer (larger = nearer) at 2x resolution, from the triangles that face the viewer
    R = 2
    zb = np.full((height * R, width * R), -np.inf)
    for _, (a, b, c), _k in faces:
        xs = np.array([a[0], b[0], c[0]]) * R
        ys = np.array([a[1], b[1], c[1]]) * R
        zs = np.array([a[2], b[2], c[2]])
        i0, i1 = max(int(np.floor(xs.min())), 0), min(int(np.ceil(xs.max())), width * R - 1)
        j0, j1 = max(int(np.floor(ys.min())), 0), min(int(np.ceil(ys.max())), height * R - 1)
        if i1 < i0 or j1 < j0:
            continue
        gx, gy = np.meshgrid(np.arange(i0, i1 + 1) + 0.5, np.arange(j0, j1 + 1) + 0.5)
        den = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(den) < 1e-12:
            continue
        w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / den
        w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        if not inside.any():
            continue
        z = w0 * zs[0] + w1 * zs[1] + w2 * zs[2]
        sub = zb[j0:j1 + 1, i0:i1 + 1]
        np.maximum(sub, np.where(inside, z, -np.inf), out=sub)

    tol = diag * 4e-3
    lines = []
    for edge in edges_of(shape):
        run = []
        for p in _edge_points(edge, diag * 0.004):
            x, y, d = screen(p)
            i, j = int(x * R), int(y * R)
            seen = True
            if 0 <= i < width * R and 0 <= j < height * R:
                near = zb[max(j - 1, 0):j + 2, max(i - 1, 0):i + 2]
                seen = d >= near.min() - tol if np.isfinite(near).all() else True
            if seen:
                run.append((x, y))
            else:
                if len(run) > 1:
                    lines.append(run)
                run = []
        if len(run) > 1:
            lines.append(run)

    def col(k):
        return "#%02x%02x%02x" % tuple(min(255, int(c * k + 18 * (1 - k))) for c in BASE)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="3D preview of the part">', '<g stroke-linejoin="round">']
    for _, (a, b, c), k in faces:
        fill = col(k)
        parts.append(f'<polygon points="{a[0]:.1f},{a[1]:.1f} {b[0]:.1f},{b[1]:.1f} {c[0]:.1f},{c[1]:.1f}" '
                     f'fill="{fill}" stroke="{fill}" stroke-width="0.6"/>')
    parts.append(f'</g><g fill="none" stroke="{EDGE}" stroke-width="1.1" stroke-linecap="round">')
    for run in lines:
        parts.append('<polyline points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in run) + '"/>')
    parts.append("</g></svg>")
    return {"svg": "".join(parts), "triangles": len(faces), "edges": len(lines)}
