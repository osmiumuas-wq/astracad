"""Machining calculations against hand-worked references, published ISO 724 thread values,
round trips, and the refusals that keep invented data out."""
import math

import pytest

from astracad_mfg import machining as M


def approx(a, b, rel=1e-6):
    return a == pytest.approx(b, rel=rel)


def test_speed_and_feed_by_hand():
    n = M.spindle_speed(200, 10).out("n")
    assert approx(n, 200000 / (math.pi * 10))                       # 6366.2 rpm
    assert approx(M.cutting_speed(n, 10).out("vc"), 200)             # round trip
    assert approx(M.cutting_speed(1000, 25.4).out("vc_sfm"), math.pi * 25.4 / 0.3048)
    assert approx(M.milling_feed(0.1, 4, 1000).out("vf"), 400)


def test_chip_thinning():
    c = M.chip_thinning(0.1, 1, 10)                                  # ae/D = 0.1 -> 2 sqrt(0.09) = 0.6
    assert approx(c.out("hex"), 0.06) and approx(c.out("fz_for_hex_equal_fz"), 0.1 / 0.6)
    assert approx(M.chip_thinning(0.1, 6, 10).out("hex"), 0.1)       # >= half the diameter: no thinning
    assert approx(M.chip_thinning(0.1, 6, 10, 45).out("hex"), 0.1 * math.sin(math.radians(45)))
    with pytest.raises(M.CalcError, match="cannot exceed"):
        M.chip_thinning(0.1, 12, 10)


def test_removal_rate_power_and_torque():
    assert approx(M.removal_rate("milling", ap_mm=5, ae_mm=2, vf_mm_min=1000).out("Q"), 10)
    q = M.removal_rate("turning", vc_m_min=200, ap_mm=2, f_mm_rev=0.2).out("Q")
    assert approx(q, 80)
    assert approx(M.removal_rate("drilling", diameter_mm=10, vf_mm_min=100).out("Q"), math.pi * 25 * 100 / 1000)
    p = M.cutting_power(q, kc_n_mm2=2000, n_rpm=1000, efficiency=0.8, source="test value")
    assert approx(p.out("Pc"), 80 * 2000 / 60000)                    # 2.667 kW
    assert approx(p.out("P_spindle"), p.out("Pc") / 0.8)
    assert approx(p.out("torque"), 60000 * p.out("Pc") / (2 * math.pi * 1000))
    k = M.cutting_power(q, kc11_n_mm2=1500, mc=0.25, hm_mm=0.2, source="test value")
    assert approx(k.out("kc"), 1500 * 0.2 ** -0.25)


def test_material_data_is_never_defaulted():
    with pytest.raises(M.CalcError, match="source"):
        M.cutting_power(80, kc_n_mm2=2000)
    with pytest.raises(M.CalcError, match="kc1.1"):
        M.cutting_power(80, source="handbook")
    with pytest.raises(M.CalcError, match="exponent"):
        M.cutting_power(80, kc11_n_mm2=1500, mc=1.5, hm_mm=0.2, source="x")


def test_turning_roughness_and_inverse():
    c = M.turning_roughness(0.2, 0.8)
    assert approx(c.out("Rt"), 6.25) and approx(c.out("Ra"), 1.5625)
    assert approx(M.turning_feed_for_ra(1.5625, 0.8).out("f_max"), 0.2)
    assert any("WARNING" in f for f in M.turning_roughness(1.0, 0.4).findings)


def test_scallop_round_trip():
    h = M.scallop_height(5, 1).out("h")
    assert approx(h, 5 - math.sqrt(24.75))
    assert approx(M.stepover_for_scallop(5, h).out("ae"), 1)
    with pytest.raises(M.CalcError):
        M.scallop_height(5, 10)


@pytest.mark.parametrize("d,p,d2,d1,d3", [(10, 1.5, 9.026, 8.376, 8.160), (6, 1.0, 5.350, 4.917, 4.773),
                                          (20, 2.5, 18.376, 17.294, 16.933)])
def test_iso_metric_basic_dimensions_match_published_values(d, p, d2, d1, d3):
    c = M.iso_metric_thread(d, p)
    assert round(c.out("d2_pitch_diameter"), 3) == d2
    assert round(c.out("D1_internal_minor"), 3) == d1
    assert round(c.out("d3_external_minor"), 3) == d3


def test_tap_drill_both_directions_and_minor_diameter_warning():
    pct = M.tap_drill(10, 1.5, drill_mm=8.5).out("percent")
    assert approx(pct, 1.5 / 1.299038 / 1.5 * 100)                   # ~77 %
    assert approx(M.tap_drill(10, 1.5, engagement_pct=pct).out("drill"), 8.5)
    assert any("below the basic minor" in f for f in M.tap_drill(10, 1.5, engagement_pct=100).findings)
    with pytest.raises(M.CalcError, match="either"):
        M.tap_drill(10, 1.5)


def test_registry_runs_every_calculation_with_record():
    c = M.run("removal_rate", {"process": "turning", "vc_m_min": "200", "ap_mm": "2", "f_mm_rev": "0.2"})
    d = c.to_dict()
    assert d["provenance"] == "CALCULATED" and d["outputs"]["Q"] == {"value": 80.0, "unit": "cm3/min"}
    assert d["equation"] and d["inputs"]["vc"]["unit"] == "m/min"
    with pytest.raises(M.CalcError, match="required"):
        M.run("spindle_speed", {"vc_m_min": "200"})
