"""Hole/shaft fit evaluation from explicit limits or from ISO 286-2 classes.

clearance_max = hole_max - shaft_min ;  clearance_min = hole_min - shaft_max
classification: clearance (min >= 0), interference (max <= 0), otherwise transition.
A zero minimum clearance (line fit, e.g. H7/h6) is classified as clearance, and flagged.
ISO 286 classes resolve through ``astracad_inspect.iso286`` — the same double-sourced table the
inspection engine uses — and an unresolved class stops the calculation with its reason.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from astracad_inspect import iso286


class FitError(ValueError):
    pass


@dataclass(frozen=True)
class FitResult:
    hole: tuple[float, float]
    shaft: tuple[float, float]
    clearance_min: float
    clearance_max: float
    kind: str
    unit: str
    note: str = ""
    source: str = "explicit limits"

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate(hole: tuple[float, float], shaft: tuple[float, float], unit: str = "mm",
             source: str = "explicit limits") -> FitResult:
    for label, (lo, hi) in (("hole", hole), ("shaft", shaft)):
        if lo is None or hi is None:
            raise FitError(f"{label} limits incomplete — a nominal is not a limit")
        if hi < lo:
            raise FitError(f"{label}: max {hi} below min {lo}")
    cmax = round(hole[1] - shaft[0], 9)
    cmin = round(hole[0] - shaft[1], 9)
    if cmin >= 0:
        kind = "clearance"
    elif cmax <= 0:
        kind = "interference"
    else:
        kind = "transition"
    note = "zero minimum clearance (line contact at MMC)" if cmin == 0 else ""
    return FitResult(hole, shaft, cmin, cmax, kind, unit, note, source)


def iso_fit(nominal_mm: float, hole_class: str, shaft_class: str) -> FitResult:
    h = iso286.resolve_fit(nominal_mm, hole_class)
    s = iso286.resolve_fit(nominal_mm, shaft_class)
    for r, want in ((h, "hole"), (s, "shaft")):
        if not r.resolved:
            raise FitError(f"{r.code}: {r.reason}")
        if r.side != want:
            raise FitError(f"{r.code} is a {r.side} class, given as the {want}")
    return evaluate(h.limits, s.limits, "mm",
                    source=f"ISO 286-2 table: {h.describe()}; {s.describe()}")
