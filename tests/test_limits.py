"""Limits and verdicts: the arithmetic the report specifies, and what it forbids."""
import pytest

from astracad_inspect.limits import (deviation_band, evaluate, resolve_fit_limits, resolve_limits,
                                     to_display_mm)
from astracad_inspect.model import Deviations, FitClass, LimitPair, NoTolerance, Symmetric
from astracad_inspect.units import UnitError


@pytest.mark.parametrize("nominal,tol,unit,want", [
    (53.0, Deviations(0.01, -0.02), "mm", (52.98, 53.01)),
    (5.0, Symmetric(0.15), "mm", (4.85, 5.15)),
    (100.0, Symmetric(0.1), "mm", (99.9, 100.1)),
    (None, LimitPair(10.05, 10.0), "mm", (10.0, 10.05)),
    (25.4, Symmetric(0.1), "in", (25.3, 25.5)),
    (45.0, Symmetric(0.5), "deg", (44.5, 45.5)),
])
def test_limits(nominal, tol, unit, want):
    assert resolve_limits(nominal, tol, unit) == want


@pytest.mark.parametrize("tol", [NoTolerance(), FitClass(hole="H12", shaft=None)])
def test_an_unstated_or_unresolved_tolerance_yields_no_limits(tol):
    assert resolve_limits(20.0, tol, "mm") is None


def test_a_loaded_iso_286_hole_class_resolves_to_its_published_limits():
    assert resolve_limits(20.0, FitClass(hole="H7", shaft=None), "mm") == (20.0, 20.021)


def test_a_loaded_iso_286_shaft_class_resolves_below_the_nominal():
    assert resolve_limits(20.0, FitClass(hole=None, shaft="g6"), "mm") == (19.98, 19.993)


def test_a_fit_pair_names_two_features_and_resolves_neither():
    """"25 H7/g6" describes a hole and a shaft together. One characteristic cannot be both, so
    picking the hole's limits would attribute a tolerance to the wrong feature."""
    assert resolve_limits(25.0, FitClass(hole="H7", shaft="g6"), "mm") is None


def test_an_inch_drawing_is_not_resolved_from_a_metric_table():
    assert resolve_limits(0.75, FitClass(hole="H7", shaft=None), "in") is None


def test_a_fit_class_without_a_nominal_size_states_no_limits():
    assert resolve_limits(None, FitClass(hole="H7", shaft=None), "mm") is None


def test_a_fit_class_band_is_not_claimed_without_a_nominal_size():
    """deviation_band() has no size to look a bracket up with, and a fit class band is
    size-dependent, so it states zero slack rather than inventing one."""
    assert deviation_band(FitClass(hole="H7", shaft=None), "mm") == 0.0


def test_a_refused_fit_class_says_why():
    limits, reason = resolve_fit_limits(450.0, FitClass(hole="H7", shaft=None), "mm")
    assert limits is None
    assert "ISO 286" in reason



def test_limit_pair_must_not_carry_a_nominal():
    with pytest.raises(AssertionError):
        resolve_limits(10.02, LimitPair(10.05, 10.0), "mm")


def test_reversed_deviation_signs_are_a_review_item_not_arithmetic():
    with pytest.raises(ValueError):
        deviation_band(Deviations(upper=-0.01, lower=0.02), "mm")


@pytest.mark.parametrize("measured,want", [
    (52.98, "pass"), (53.01, "pass"), (53.0, "pass"),
    (52.979, "fail"), (53.011, "fail"),
])
def test_limits_are_inclusive(measured, want):
    assert evaluate(measured, (52.98, 53.01), "mm") == want


def test_no_limits_means_no_verdict():
    assert evaluate(53.0, None, "mm") == "review_required"


def test_boundary_allowance_must_be_declared():
    assert evaluate(52.979, (52.98, 53.01), "mm") == "fail"
    assert evaluate(52.979, (52.98, 53.01), "mm",
                    tolerance_for_boundary=0.002) == "boundary_review"


def test_degrees_have_no_millimetre_display():
    with pytest.raises(UnitError):
        to_display_mm(45.0, "deg")


def test_band_widths():
    assert deviation_band(Symmetric(0.1), "mm") == pytest.approx(0.2)
    assert deviation_band(Deviations(0.05, -0.02), "mm") == pytest.approx(0.07)
    assert deviation_band(LimitPair(10.05, 10.0), "mm") == pytest.approx(0.05)
    assert deviation_band(NoTolerance(), "mm") == 0.0
