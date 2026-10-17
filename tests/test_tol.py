"""Tolerance engine against hand-derived references (independent of the implementation)."""
import math
import random

import pytest

from astracad_tol import fits, selective
from astracad_tol.stack import Contributor, StackError, monte_carlo, rss, worst_case


def classic(dist=None, k=3.0):
    """Housing 50±0.1 minus parts 20±0.05 and 29.8±0.05: gap 0.2 nominal."""
    kw = dict(distribution=dist, sigma_multiple=k)
    return [Contributor("housing", 50, 49.9, 50.1, "mm", +1, **kw),
            Contributor("part_a", 20, 19.95, 20.05, "mm", -1, **kw),
            Contributor("part_b", 29.8, 29.75, 29.85, "mm", -1, **kw)]


def test_worst_case_reference():
    r = worst_case(classic(), spec=(0.0, None))
    assert (r.nominal_gap, r.minimum, r.maximum) == pytest.approx((0.2, 0.0, 0.4))
    assert r.verdict == "meets"
    assert worst_case(classic(), spec=(0.05, None)).verdict == "violates"


def test_rss_reference():
    r = rss(classic())
    sigma = math.sqrt((0.1 / 3) ** 2 + 2 * (0.05 / 3) ** 2)
    assert r.sigma == pytest.approx(sigma, rel=1e-12)
    assert (r.minimum, r.maximum) == pytest.approx((0.2 - 3 * sigma, 0.2 + 3 * sigma))
    assert r.contributions["housing"] == pytest.approx(100 * 0.01 / 0.015)


def test_rss_carries_mean_shift():
    cs = [Contributor("a", 10, 10.1, 10.2, "mm", 1, sigma_multiple=3),     # 10 +0.2/+0.1
          Contributor("b", 5, 4.95, 5.05, "mm", -1, sigma_multiple=3)]
    r = rss(cs)
    assert r.nominal_gap == pytest.approx(5.0) and r.mean == pytest.approx(5.15)


def test_rss_requires_stated_sigma_multiple():
    with pytest.raises(StackError, match="sigma_multiple"):
        rss(classic(k=None))


def test_mc_normal_matches_rss_closed_form():
    r = monte_carlo(classic("normal"), 200_000, seed=7, spec=(0.0, 0.4))
    sigma = math.sqrt((0.1 / 3) ** 2 + 2 * (0.05 / 3) ** 2)
    assert r.mean == pytest.approx(0.2, abs=5e-4)
    assert r.sigma == pytest.approx(sigma, rel=0.01)
    lo, hi = r.fraction_outside_ci95
    assert lo <= r.fraction_outside <= hi and hi < 1e-4


def test_mc_uniform_sigma_reference():
    r = monte_carlo(classic("uniform"), 100_000, seed=3)
    want = math.sqrt((0.2 ** 2 + 2 * 0.1 ** 2) / 12)
    assert r.sigma == pytest.approx(want, rel=0.01)


def test_mc_is_reproducible_by_seed():
    a = monte_carlo(classic("triangular"), 5000, seed=11)
    b = monte_carlo(classic("triangular"), 5000, seed=11)
    assert (a.mean, a.sigma, a.minimum) == (b.mean, b.sigma, b.minimum)


def test_mc_requires_distribution():
    with pytest.raises(StackError, match="distribution"):
        monte_carlo(classic(None), 1000, seed=1)


def test_bounded_distributions_never_leave_worst_case_band():
    rng = random.Random(0)
    for _ in range(20):
        cs = []
        for i in range(rng.randint(1, 6)):
            n = rng.uniform(1, 100)
            lo, hi = n - rng.uniform(0, 0.5), n + rng.uniform(0, 0.5)
            cs.append(Contributor(f"c{i}", n, lo, hi, "mm", rng.choice([1, -1]),
                                  distribution=rng.choice(["uniform", "triangular"])))
        wc = worst_case(cs)
        mc = monte_carlo(cs, 500, seed=rng.randint(0, 10**6))
        assert wc.minimum - 1e-9 <= mc.minimum and mc.maximum <= wc.maximum + 1e-9


def test_rejections():
    with pytest.raises(StackError, match="mixed units"):
        worst_case([Contributor("a", 1, 0.9, 1.1, "mm"), Contributor("b", 1, 0.9, 1.1, "in")])
    with pytest.raises(StackError, match="not substituted"):
        worst_case([Contributor("a", 1, None, 1.1, "mm")])
    with pytest.raises(StackError, match="below"):
        worst_case([Contributor("a", 1, 1.2, 1.1, "mm")])
    with pytest.raises(StackError):
        worst_case([])


def test_from_deviations():
    c = Contributor.from_deviations("x", 25.4, 0.0, -0.1, "mm")
    assert (c.lower, c.upper) == (25.3, 25.4)


# ---- fits ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("shaft,kind,cmin,cmax", [
    ("g6", "clearance", 0.007, 0.041),     # H7 0/+21, g6 -7/-20 um (18-30 mm)
    ("k6", "transition", -0.015, 0.019),   # k6 +15/+2
    ("p6", "interference", -0.035, -0.001),  # p6 +35/+22
    ("h6", "clearance", 0.0, 0.034),       # h6 0/-13
])
def test_iso_fits_20mm(shaft, kind, cmin, cmax):
    r = fits.iso_fit(20, "H7", shaft)
    assert r.kind == kind
    assert (r.clearance_min, r.clearance_max) == pytest.approx((cmin, cmax), abs=1e-9)


def test_iso_fit_wrong_side_and_unresolved():
    with pytest.raises(fits.FitError, match="hole class"):
        fits.iso_fit(20, "H7", "H7")
    with pytest.raises(fits.FitError):
        fits.iso_fit(900, "H7", "g6")


# ---- selective assembly -------------------------------------------------------------------------
def test_selective_equal_bands_reduce_range_by_n():
    p = selective.plan((25.000, 25.012), (24.988, 25.000), 3)
    assert p.equal_widths and p.interchangeable == pytest.approx((0.0, 0.024))
    for g in p.groups:
        assert (g.clearance_min, g.clearance_max) == pytest.approx((0.008, 0.016))
    assert p.selective == pytest.approx((0.008, 0.016))


def test_selective_unequal_bands_are_flagged():
    p = selective.plan((20.000, 20.021), (19.980, 19.993), 3)
    assert not p.equal_widths and p.notes


def test_match_measured_pairs_rejects_and_surplus():
    holes = {"H1": 25.001, "H2": 25.005, "H3": 25.011, "H4": 25.013}
    shafts = {"S1": 24.989, "S2": 24.993, "S3": 24.999, "S4": 24.990}
    m = selective.match_measured(holes, shafts, (25.000, 25.012), (24.988, 25.000), 3)
    assert [r["serial"] for r in m.rejected] == ["H4"]
    assert len(m.pairs) == 3 and {s["serial"] for s in m.surplus} == {"S4"}
    assert all(0.008 - 1e-9 <= p["clearance"] <= 0.016 + 1e-9 for p in m.pairs)
