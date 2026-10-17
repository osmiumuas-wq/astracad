"""Inspection (metrology) calculations, in the same traceable form as the machining ones.

  true position (2D, diametral zone, ASME Y14.5 convention)
        position = 2 sqrt(dx^2 + dy^2),  dx = x_actual - x_basic, dy = y_actual - y_basic
        bonus at MMC: hole  = actual size - MMC size (minimum) ; pin/shaft = MMC size (maximum) - actual size
        bonus at LMC: hole  = LMC size (maximum) - actual size ; pin/shaft = actual size - LMC size (minimum)
        allowed = stated tolerance + bonus ; conforms when position <= allowed
        (datum-feature shift ("datum mobility") is NOT included: it needs the datum features' sizes)
  three-wire thread measurement, 60 deg threads (ISO metric, Unified), lead angle neglected
        M = E + 3 W - 0.866025 P   ->   E = M - 3 W + 0.866025 P
        best wire W = 0.577350 P ; usable wires 0.505 P .. 1.010 P (they must touch the flanks)
  sine bar      H = L sin(theta) ;  theta = asin(H / L)
  bolt circle   x_i = cx + (D/2) cos(a0 + i 360/n) ; y_i = cy + (D/2) sin(...) ; chord = D sin(180/n)
  cycle time    turning / drilling: t = (L + approach) / (f n) * passes ; milling: t = (L + approach) / vf * passes
  unit conversion: exact SI definitions (1 in = 25.4 mm, 1 ft = 0.3048 m)
"""
from __future__ import annotations

import math

from .machining import CalcError, Calculation, _pos


def _num(name, v, unit):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise CalcError(f"{name} is required ({unit})") from None
    if not math.isfinite(x):
        raise CalcError(f"{name} must be finite ({unit})")
    return x


def true_position(x_actual, y_actual, x_basic, y_basic, tolerance_mm, modifier="RFS", feature="hole",
                  size_actual=None, size_min=None, size_max=None) -> Calculation:
    xa, ya = _num("x actual", x_actual, "mm"), _num("y actual", y_actual, "mm")
    xb, yb = _num("x basic", x_basic, "mm"), _num("y basic", y_basic, "mm")
    tol = _pos("position tolerance", tolerance_mm, "mm", allow_zero=True)
    mod = str(modifier or "RFS").upper()
    feat = str(feature or "hole").lower()
    if mod not in ("RFS", "MMC", "LMC"):
        raise CalcError("modifier must be RFS, MMC or LMC")
    if feat not in ("hole", "pin", "shaft"):
        raise CalcError("feature must be hole, pin or shaft")
    dx, dy = xa - xb, ya - yb
    pos = 2 * math.hypot(dx, dy)
    inputs = {"x_actual": (xa, "mm"), "y_actual": (ya, "mm"), "x_basic": (xb, "mm"), "y_basic": (yb, "mm"),
              "tolerance": (tol, "mm"), "modifier": (0.0 if mod == "RFS" else 1.0 if mod == "MMC" else 2.0, mod)}
    bonus = 0.0
    findings = []
    if mod != "RFS":
        s = _pos("actual size", size_actual, "mm")
        lo, hi = _pos("size minimum", size_min, "mm"), _pos("size maximum", size_max, "mm")
        if hi < lo:
            raise CalcError("size maximum is below the minimum")
        inputs.update({"size_actual": (s, "mm"), "size_min": (lo, "mm"), "size_max": (hi, "mm")})
        if not (lo - 1e-9 <= s <= hi + 1e-9):
            findings.append(f"WARNING: actual size {s:g} is outside its limits {lo:g}..{hi:g}: the feature "
                            "is nonconforming on size; no bonus applies")
        else:
            hole = feat == "hole"
            if mod == "MMC":
                bonus = (s - lo) if hole else (hi - s)
            else:
                bonus = (hi - s) if hole else (s - lo)
    allowed = tol + bonus
    ok = pos <= allowed + 1e-12 and not any("nonconforming" in f for f in findings)
    c = Calculation("true position (2D)", "position = 2 sqrt(dx^2 + dy^2) ; allowed = tolerance + bonus",
                    inputs, {"dx": (dx, "mm"), "dy": (dy, "mm"), "position": (pos, "mm"), "bonus": (bonus, "mm"),
                             "allowed": (allowed, "mm"), "conforms": (1.0 if ok else 0.0, "PASS" if ok else "FAIL")},
                    ["diametral (cylindrical) tolerance zone; coordinates measured in the datum reference frame",
                     "datum-feature shift is not included"], findings)
    return c


def three_wire(measurement_mm, wire_mm, pitch_mm) -> Calculation:
    m, w, p = (_pos("measurement over wires M", measurement_mm, "mm"), _pos("wire diameter W", wire_mm, "mm"),
               _pos("pitch P", pitch_mm, "mm"))
    e = m - 3 * w + 0.866025 * p
    c = Calculation("three-wire pitch diameter (60 deg thread)", "E = M - 3 W + 0.866025 P",
                    {"M": (m, "mm"), "W": (w, "mm"), "P": (p, "mm")},
                    {"E_pitch_diameter": (e, "mm"), "best_wire": (0.57735 * p, "mm")},
                    ["60 deg symmetric thread form", "lead-angle and wire-deformation corrections neglected"])
    if not (0.505 * p <= w <= 1.010 * p):
        c.findings.append(f"WARNING: wire {w:g} mm is outside 0.505P..1.010P ({0.505 * p:.4f}..{1.010 * p:.4f}); "
                          "it does not touch the flanks correctly")
    return c


def sine_bar(length_mm, angle_deg=None, height_mm=None) -> Calculation:
    L = _pos("sine bar length L", length_mm, "mm")
    if (angle_deg is None) == (height_mm is None):
        raise CalcError("give either the angle or the gauge-block height")
    if angle_deg is not None:
        a = _num("angle", angle_deg, "deg")
        if not (0 < a < 90):
            raise CalcError("angle must be between 0 and 90 deg")
        h = L * math.sin(math.radians(a))
        c = Calculation("sine bar - gauge block height", "H = L sin(theta)", {"L": (L, "mm"), "theta": (a, "deg")}, {"H": (h, "mm")})
    else:
        h = _pos("gauge-block height H", height_mm, "mm")
        if h >= L:
            raise CalcError("height must be below the sine bar length")
        a = math.degrees(math.asin(h / L))
        c = Calculation("sine bar - angle from height", "theta = asin(H / L)", {"L": (L, "mm"), "H": (h, "mm")}, {"theta": (a, "deg")})
    c.assumptions.append("L is the roll centre distance; above ~45 deg small height errors give large angle errors")
    if a > 45:
        c.findings.append("WARNING: above 45 deg the sine bar becomes sensitive; consider the complementary setup")
    return c


def bolt_circle(pcd_mm, holes, start_angle_deg=0.0, cx_mm=0.0, cy_mm=0.0) -> Calculation:
    d = _pos("pitch circle diameter", pcd_mm, "mm")
    n = _pos("number of holes", holes, "-")
    if n != int(n) or n < 2 or n > 360:
        raise CalcError("number of holes is a whole number from 2 to 360")
    n = int(n)
    a0, cx, cy = _num("start angle", start_angle_deg, "deg"), _num("centre x", cx_mm, "mm"), _num("centre y", cy_mm, "mm")
    out = {}
    for i in range(n):
        a = math.radians(a0 + i * 360.0 / n)
        out[f"hole_{i + 1}_x"] = (cx + d / 2 * math.cos(a), "mm")
        out[f"hole_{i + 1}_y"] = (cy + d / 2 * math.sin(a), "mm")
    out["chord"] = (d * math.sin(math.pi / n), "mm")
    return Calculation("bolt circle coordinates", "x = cx + D/2 cos(a0 + i 360/n) ; y = cy + D/2 sin(...) ; chord = D sin(180/n)",
                       {"D": (d, "mm"), "n": (float(n), "-"), "a0": (a0, "deg"), "cx": (cx, "mm"), "cy": (cy, "mm")}, out,
                       ["angles counter-clockwise from +X"])


def cycle_time(process, length_mm, *, feed_mm_rev=None, n_rpm=None, vf_mm_min=None, approach_mm=0.0, passes=1) -> Calculation:
    L = _pos("cut length L", length_mm, "mm")
    ap = _pos("approach + overrun", approach_mm, "mm", allow_zero=True)
    k = _pos("passes", passes, "-")
    if k != int(k):
        raise CalcError("passes is a whole number")
    if process in ("turning", "drilling"):
        f, n = _pos("feed f", feed_mm_rev, "mm/rev"), _pos("spindle speed n", n_rpm, "rpm")
        t = (L + ap) / (f * n) * k
        return Calculation(f"cutting time - {process}", "t = (L + approach) / (f n) x passes",
                           {"L": (L, "mm"), "approach": (ap, "mm"), "f": (f, "mm/rev"), "n": (n, "rpm"), "passes": (k, "-")},
                           {"t": (t, "min"), "t_s": (t * 60, "s")}, ["cutting time only: no rapid, tool change or load time"])
    if process == "milling":
        vf = _pos("table feed vf", vf_mm_min, "mm/min")
        t = (L + ap) / vf * k
        return Calculation("cutting time - milling", "t = (L + approach) / vf x passes",
                           {"L": (L, "mm"), "approach": (ap, "mm"), "vf": (vf, "mm/min"), "passes": (k, "-")},
                           {"t": (t, "min"), "t_s": (t * 60, "s")}, ["cutting time only: no rapid, tool change or load time"])
    raise CalcError("process must be turning, drilling or milling")


_CONV = {  # to SI base of each quantity
    "mm": ("length", 1e-3), "in": ("length", 0.0254), "um": ("length", 1e-6), "m": ("length", 1.0), "ft": ("length", 0.3048),
    "m/min": ("speed", 1 / 60), "ft/min": ("speed", 0.3048 / 60), "sfm": ("speed", 0.3048 / 60),
    "mm/rev": ("feed", 1e-3), "in/rev": ("feed", 0.0254),
    "mm/min": ("rate", 1e-3 / 60), "in/min": ("rate", 0.0254 / 60), "ipm": ("rate", 0.0254 / 60),
    "n/mm2": ("stress", 1e6), "mpa": ("stress", 1e6), "psi": ("stress", 6894.757293168),
    "kw": ("power", 1e3), "hp": ("power", 745.6998715822702),
}


def convert(value, from_unit, to_unit) -> Calculation:
    v = _num("value", value, str(from_unit))
    a, b = str(from_unit).strip().lower(), str(to_unit).strip().lower()
    if a not in _CONV or b not in _CONV:
        raise CalcError(f"units supported: {', '.join(sorted(_CONV))}")
    if _CONV[a][0] != _CONV[b][0]:
        raise CalcError(f"{a} ({_CONV[a][0]}) cannot be converted to {b} ({_CONV[b][0]})")
    out = v * _CONV[a][1] / _CONV[b][1]
    return Calculation("unit conversion", f"{b} = {a} x {_CONV[a][1]:.12g} / {_CONV[b][1]:.12g}",
                       {"value": (v, a)}, {"value": (out, b)},
                       ["exact definitions; hp is the mechanical horsepower (745.7 W)"])


REGISTRY = {
    "true_position": (true_position, [("x_actual", "X actual", "mm"), ("y_actual", "Y actual", "mm"), ("x_basic", "X basic", "mm"),
                                      ("y_basic", "Y basic", "mm"), ("tolerance_mm", "Position tolerance (dia.)", "mm"),
                                      ("modifier", "Modifier (RFS / MMC / LMC)", ""), ("feature", "Feature (hole / pin)", ""),
                                      ("size_actual", "Actual size (MMC/LMC)", "mm"), ("size_min", "Size minimum", "mm"),
                                      ("size_max", "Size maximum", "mm")]),
    "three_wire": (three_wire, [("measurement_mm", "Measurement over wires M", "mm"), ("wire_mm", "Wire diameter W", "mm"),
                                ("pitch_mm", "Pitch P", "mm")]),
    "sine_bar": (sine_bar, [("length_mm", "Sine bar length L", "mm"), ("angle_deg", "Angle (or height)", "deg"),
                            ("height_mm", "Gauge block height", "mm")]),
    "bolt_circle": (bolt_circle, [("pcd_mm", "Pitch circle diameter", "mm"), ("holes", "Number of holes", "-"),
                                  ("start_angle_deg", "Start angle", "deg"), ("cx_mm", "Centre X", "mm"), ("cy_mm", "Centre Y", "mm")]),
    "cycle_time": (cycle_time, [("process", "Process (turning / drilling / milling)", ""), ("length_mm", "Cut length L", "mm"),
                                ("feed_mm_rev", "Feed f (turning/drilling)", "mm/rev"), ("n_rpm", "Spindle speed n", "rpm"),
                                ("vf_mm_min", "Table feed vf (milling)", "mm/min"), ("approach_mm", "Approach + overrun", "mm"),
                                ("passes", "Passes", "-")]),
    "convert": (convert, [("value", "Value", ""), ("from_unit", "From unit", ""), ("to_unit", "To unit", "")]),
}
