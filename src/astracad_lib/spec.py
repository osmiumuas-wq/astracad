"""Small builders for Part Language v2 specs, shared by the generators."""
from __future__ import annotations

import math


def plane(id: str, origin, normal, x_dir) -> dict:
    return {"$op": "ref.plane", "id": id, "args": {"origin": [float(c) for c in origin], "normal": [float(c) for c in normal],
                                                    "x_dir": [float(c) for c in x_dir]}}


def plane_x(id: str, x: float) -> dict:
    """A plane normal to +X at x, its frame u = +Y, v = +Z."""
    return plane(id, [x, 0, 0], [1, 0, 0], [0, 1, 0])


def loops(id: str, on: str, lps) -> dict:
    return {"$op": "sketch.loops", "id": id, "sketched_on": on, "args": {"loops": lps}}


def circle(cu: float, cv: float, r: float) -> list:
    return [["C", float(cu), float(cv), float(r)]]


def polygon(points) -> list:
    n = len(points)
    return [["L", float(points[i][0]), float(points[i][1]), float(points[(i + 1) % n][0]), float(points[(i + 1) % n][1])]
            for i in range(n)]


def slot(c1, r1: float, c2, r2: float) -> list:
    """The outline around two circles joined by their outer tangents (a crank web, a connecting rod), counter-clockwise."""
    du, dv = c2[0] - c1[0], c2[1] - c1[1]
    ln = math.hypot(du, dv)
    if ln <= abs(r1 - r2):
        raise ValueError("one circle contains the other")
    e, p = (du / ln, dv / ln), (-dv / ln, du / ln)
    s = (r1 - r2) / ln
    c = math.sqrt(1 - s * s)

    def at(center, r, sign):           # tangent point: centre + r (sin g, sign cos g) in the (e, p) frame
        return (center[0] + r * (s * e[0] + sign * c * p[0]), center[1] + r * (s * e[1] + sign * c * p[1]))
    t1u, t1l, t2u, t2l = at(c1, r1, 1), at(c1, r1, -1), at(c2, r2, 1), at(c2, r2, -1)
    return [["L", *t1l, *t2l], ["A", c2[0], c2[1], *t2l, *t2u, 1], ["L", *t2u, *t1u], ["A", c1[0], c1[1], *t1u, *t1l, 1]]


def extrude(id: str, depth: float, **a) -> dict:
    return {"$op": "feature.extrude", "id": id, "args": {"depth": float(depth), **a}}


def cut(id: str, depth: float | None = None, **a) -> dict:
    args = dict(a)
    if depth is not None:
        args["depth"] = float(depth)
    return {"$op": "feature.cut", "id": id, "args": args}


def frame(origin, x, z) -> dict:
    return {"origin": [float(c) for c in origin], "x": [float(c) for c in x], "z": [float(c) for c in z]}


def part(name: str, features: list, interfaces: dict) -> dict:
    return {"name": name, "units": "mm", "language": 2, "features": features, "interfaces": interfaces,
            "exports": [{"format": "step"}]}
