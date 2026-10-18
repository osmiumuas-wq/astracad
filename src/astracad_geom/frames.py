"""Frames and sketch geometry in plain Python - the parts of Part Language v2 that need no kernel.

A frame is an origin and two unit axes x, y of a plane; n = x cross y. Sketch point (u, v) -> O + u x + v y.
Named planes follow docs/engineering/part-language-v2.md section 1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

Vec = tuple[float, float, float]
TOL = 1e-9


def add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a: Vec) -> float:
    return math.sqrt(dot(a, a))


def unit(a, what: str = "vector") -> Vec:
    if not (isinstance(a, (list, tuple)) and len(a) == 3 and all(isinstance(c, (int, float)) and
                                                                  not isinstance(c, bool) for c in a)):
        raise ValueError(f"{what} must be [x, y, z]")
    v = (float(a[0]), float(a[1]), float(a[2]))
    n = norm(v)
    if not math.isfinite(n) or n < 1e-12:
        raise ValueError(f"{what} has no direction")
    return mul(v, 1.0 / n)


AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def axis(spec, what: str = "direction") -> Vec:
    """'X' / 'Y' / 'Z' (optionally signed, '+Z', '-X') or a vector."""
    if isinstance(spec, str):
        s = spec.strip().upper()
        sign = -1.0 if s.startswith("-") else 1.0
        key = s.lstrip("+-")
        if key not in AXES:
            raise ValueError(f"{what} must be X, Y, Z or a vector, not {spec!r}")
        return mul(AXES[key], sign)
    return unit(spec, what)


@dataclass(frozen=True)
class Frame:
    origin: Vec
    x: Vec
    y: Vec

    @property
    def n(self) -> Vec:
        return cross(self.x, self.y)

    def point(self, u: float, v: float) -> Vec:
        return add(self.origin, add(mul(self.x, u), mul(self.y, v)))

    def vector(self, u: float, v: float) -> Vec:
        return add(mul(self.x, u), mul(self.y, v))

    def moved(self, d: Vec) -> "Frame":
        return Frame(add(self.origin, d), self.x, self.y)


NAMED: dict[str, Frame] = {
    "front": Frame((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
    "top": Frame((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0)),
    "right": Frame((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
}


def plane_frame(args: dict, frames: dict[str, Frame]) -> Frame:
    """The frame a `ref.plane` defines (explicit origin/normal/x_dir, or base + offset)."""
    if "base" in args:
        base = frames.get(str(args["base"]))
        if base is None:
            raise ValueError(f"unknown base plane {args['base']!r}")
        off = float(args.get("offset", 0.0))
        return base.moved(mul(base.n, off))
    origin = args.get("origin")
    if not (isinstance(origin, (list, tuple)) and len(origin) == 3):
        raise ValueError("ref.plane needs origin [x, y, z] (or base + offset)")
    n = unit(args.get("normal"), "normal")
    x = unit(args.get("x_dir"), "x_dir")
    if abs(dot(n, x)) > TOL:
        raise ValueError("ref.plane x_dir is not perpendicular to normal")
    y = cross(n, x)
    return Frame((float(origin[0]), float(origin[1]), float(origin[2])), x, y)


# ---- sketch loops (u, v) ------------------------------------------------------------------------

def arc_sweep(seg) -> float:
    """Signed sweep of an 'A' segment from start to end in its stated direction, in (0, 2 pi]."""
    _, cu, cv, su, sv, eu, ev, d = seg
    t0, t1 = math.atan2(sv - cv, su - cu), math.atan2(ev - cv, eu - cu)
    s = t1 - t0
    if d > 0:
        while s <= 0:
            s += 2 * math.pi
        while s > 2 * math.pi:
            s -= 2 * math.pi
    else:
        while s >= 0:
            s -= 2 * math.pi
        while s < -2 * math.pi:
            s += 2 * math.pi
    return s


def loop_area(loop) -> float:
    """Signed area by Green's theorem; arcs exact (as LoopMath.SignedArea in mac)."""
    a = 0.0
    for s in loop:
        if s[0] == "L":
            a += (s[1] * s[4] - s[3] * s[2]) / 2
        elif s[0] == "C":
            a += math.pi * s[3] * s[3]
        else:
            cu, cv = s[1], s[2]
            r = math.hypot(s[3] - cu, s[4] - cv)
            t0, d = math.atan2(s[4] - cv, s[3] - cu), arc_sweep(s)
            t1 = t0 + d
            a += (r * r * d + r * (cu * (math.sin(t1) - math.sin(t0)) - cv * (math.cos(t1) - math.cos(t0)))) / 2
    return a


def rectangle_loop(width: float, height: float, x: float = 0.0, y: float = 0.0):
    return [["L", x, y, x + width, y], ["L", x + width, y, x + width, y + height],
            ["L", x + width, y + height, x, y + height], ["L", x, y + height, x, y]]


def polygon_loop(points):
    return [["L", points[i][0], points[i][1], points[(i + 1) % len(points)][0], points[(i + 1) % len(points)][1]]
            for i in range(len(points))]
