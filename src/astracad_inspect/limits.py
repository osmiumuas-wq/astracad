"""Deterministic dimensional limit calculation.

Limits are only produced when the drawing states enough to compute them. An absent tolerance
yields None; an ISO 286-2 letter code yields the limits its published deviation table states for
that nominal size, or None with a reason. The caller then blocks approval rather than
substituting the nominal, which would turn "not stated" into "stated as exact".
"""
from __future__ import annotations

from . import iso286
from .model import (Deviations, FitClass, GeometricTolerance, LimitPair, NoTolerance, Symmetric,
                    Tolerance)
from .units import UnitError, assert_same_unit, to_mm

LENGTH_UNITS = {"mm", "cm", "m", "in", "ft", "µm", "um"}


def _is_length(unit: str) -> bool:
    return unit in LENGTH_UNITS


def deviation_band(tolerance: Tolerance, unit: str) -> float:
    """Total width of the tolerance zone, in the same unit. Used as the slack when checking a
    written value against drawn geometry: geometry may lawfully sit anywhere in the band."""
    if unit != "deg" and not _is_length(unit):
        raise UnitError(f"cannot evaluate a tolerance in unit {unit!r}")
    if isinstance(tolerance, NoTolerance):
        return 0.0
    if isinstance(tolerance, Symmetric):
        if tolerance.plus_minus < 0:
            raise ValueError("a ± tolerance band cannot be negative")
        return 2 * tolerance.plus_minus
    if isinstance(tolerance, Deviations):
        if tolerance.upper < 0 or tolerance.lower > 0:
            raise ValueError(
                f"deviations violate the sign convention (upper={tolerance.upper}, "
                f"lower={tolerance.lower}); the callout needs review rather than arithmetic")
        return tolerance.upper - tolerance.lower
    if isinstance(tolerance, LimitPair):
        if tolerance.maximum < tolerance.minimum:
            raise ValueError("limit pair has maximum below minimum")
        return tolerance.maximum - tolerance.minimum
    if isinstance(tolerance, GeometricTolerance):
        if tolerance.zone < 0:
            raise ValueError("a geometric tolerance zone cannot be negative")
        return tolerance.zone
    if isinstance(tolerance, FitClass):
        # A letter code's zone is fixed in micrometres per nominal bracket, so its width depends
        # on a size this function is not given. Callers that have the size use resolve_limits.
        return 0.0
    raise TypeError(f"unhandled tolerance representation: {tolerance!r}")


def _round_limit(value: float) -> float:
    """Cut binary-float representation error out of a derived limit (25.4-0.1 stores as
    25.299999999999997). Nine decimals is a nanometre: below every tolerance zone a drawing
    can state, so this sharpens the number without changing it. Limits copied straight from a
    limit pair are NOT rounded - they are returned exactly as the drawing writes them."""
    return round(value, 9)


def resolve_fit_limits(nominal: float | None, fit: FitClass, unit: str
                       ) -> tuple[tuple[float, float] | None, str | None]:
    """Limits for an ISO 286-2 class, and the reason it was refused.

    A letter code is a deviation band, not a pair of limits, so it resolves only for one
    feature at one nominal size in millimetres. Every other case refuses and says why rather
    than borrowing a neighbouring column or falling back to the nominal.
    """
    if unit != "mm":
        return None, (f"ISO 286-2 deviations are published in millimetres, so a {unit!r} "
                      f"drawing is not resolved from the metric table")
    if fit.hole and fit.shaft:
        return None, (f"fit {fit.hole}/{fit.shaft} names a hole and a shaft: one characteristic "
                      f"carries one class, so neither is resolved")
    code = fit.hole or fit.shaft
    if not code:
        return None, "no ISO 286-2 class designation was stated to look up"
    if nominal is None:
        return None, (f"ISO 286-2 class {code} is a deviation band, so its limits depend on the "
                      f"nominal size, which the callout does not state")
    got = iso286.resolve_fit(nominal, code)
    return got.limits, got.reason


def resolve_limits(nominal: float | None, tolerance: Tolerance, unit: str
                   ) -> tuple[float, float] | None:
    """Return (minimum, maximum) inclusive, or None when the source does not state enough.

    A limit pair carries no nominal and is returned exactly as written: re-deriving a nominal
    from it would invent a dimension the drawing never declared.
    """
    if isinstance(tolerance, NoTolerance):
        return None
    if isinstance(tolerance, FitClass):
        limits, _ = resolve_fit_limits(nominal, tolerance, unit)
        return limits
    if isinstance(tolerance, GeometricTolerance):
        # A frame states a zone width: the measured deviation must lie in 0 .. zone. MMC/LMC bonus
        # depends on the produced size and is deliberately not added here.
        if nominal is not None:
            raise AssertionError("a geometric tolerance row carries no nominal")
        return (0.0, tolerance.zone)

    if isinstance(tolerance, LimitPair):
        if nominal is not None:
            raise AssertionError("a limit pair must not carry a nominal")
        return (tolerance.minimum, tolerance.maximum)

    if nominal is None:
        return None
    if isinstance(tolerance, Symmetric):
        return (_round_limit(nominal - tolerance.plus_minus),
                _round_limit(nominal + tolerance.plus_minus))
    if isinstance(tolerance, Deviations):
        return (_round_limit(nominal + tolerance.lower),
                _round_limit(nominal + tolerance.upper))
    raise TypeError(f"unhandled tolerance representation: {tolerance!r}")


def evaluate(measured: float, limits: tuple[float, float] | None, unit: str,
             *, tolerance_for_boundary: float = 0.0) -> str:
    """Inclusive pass/fail against approved limits, per the project report §10.

    Missing limits block the verdict rather than defaulting to pass. The boundary allowance is
    for measurement-system uncertainty and must be declared, not assumed.
    """
    assert_same_unit(unit, unit, "evaluate")
    if limits is None:
        return "review_required"
    lo, hi = limits
    margin = abs(tolerance_for_boundary)
    if measured < lo - margin or measured > hi + margin:
        return "fail"
    if measured < lo or measured > hi:
        return "boundary_review"
    return "pass"


def to_display_mm(value: float | None, unit: str) -> float | None:
    """Millimetre display value. Angular characteristics have no length meaning, so the
    conversion refuses rather than printing degrees under a mm column."""
    if value is None:
        return None
    if unit == "deg":
        raise UnitError("an angular characteristic has no millimetre value")
    return to_mm(value, unit)
