"""Costing: every number sourced and dated, nothing defaulted, result equal to a hand calculation."""
import copy
from datetime import date
from pathlib import Path

import pytest

from astracad_mfg.costing import CostingError, Geometry, estimate, geometry_from_evidence
from astracad_mfg.report import verify, write

SWP = Path(__file__).parent / "fixtures" / "sw_part"


def S(v, unit, src="test rate book", as_of="2026-10-01"):
    return {"value": v, "unit": unit, "source": src, "as_of": as_of}


RATES = {
    "currency": "INR",
    "materials": {"AL6061-T6": {"density_g_cm3": S(2.70, "g/cm^3", "ASM handbook value"),
                                "price_per_kg": S(300.0, "INR/kg", "supplier quote Q-17")}},
    "machines": {"VMC-3ax": {"rate_per_hour": S(900.0, "INR/h", "shop rate sheet")}},
    "labour_rate_per_hour": S(300.0, "INR/h"),
    "overhead_fraction": S(0.20, "1"),
    "scrap_fraction": S(0.05, "1"),
}
JOB = {"material": "AL6061-T6", "machine": "VMC-3ax", "quantity": 50,
       "stock_allowance_mm": S(2.0, "mm", "user"), "setup_time_min": S(30.0, "min", "user"),
       "operator_attendance": S(0.5, "1", "user"),
       "mrr_cm3_per_min": S(5.0, "cm^3/min", "user, roughing in Al"),
       "handling_time_min": S(2.0, "min", "user")}
TODAY = date(2026, 10, 17)


@pytest.fixture(scope="module")
def geo():
    return geometry_from_evidence(SWP / "plate_4holes.report.json", SWP / "plate_4holes.step")


def test_geometry_comes_from_kernel_volume_and_step_bbox(geo):
    assert geo.size_mm == (100.0, 40.0, 10.0)
    assert abs(geo.volume_mm3 - 38869.026645) < 1e-5
    assert "sha256:" in geo.volume_source and "sha256:" in geo.size_source


def test_matches_hand_calculation(geo):
    e = estimate(geo, RATES, JOB, today=TODAY)
    # written out independently of the engine
    stock = 104 * 44 * 14
    material = stock / 1000 * 2.70 / 1000 * 300.0
    cycle = (stock - 38869.02664470769) / 1000 / 5.0 + 2.0
    machine = cycle / 60 * 900.0
    labour = cycle / 60 * 300.0 * 0.5
    setup = 0.5 * (900.0 + 300.0) / 50
    per_part = (material + machine + labour + setup) / 0.95 * 1.20
    assert e.cost_per_part == pytest.approx(per_part, rel=1e-6)
    assert e.batch_cost == pytest.approx(per_part * 50, rel=1e-6)
    assert e.price_per_part is None                    # no margin supplied, none invented
    assert not e.warnings


def test_every_line_has_a_formula_and_every_input_a_source(geo):
    e = estimate(geo, RATES, JOB, today=TODAY)
    assert all(ln.formula for ln in e.lines)
    for v in e.inputs.values():
        if isinstance(v, dict):
            assert v["source"] and v["as_of"]


@pytest.mark.parametrize("path,label", [
    (("rates", "materials", "AL6061-T6", "price_per_kg"), "material price"),
    (("rates", "labour_rate_per_hour"), "labour rate"),
    (("job", "setup_time_min"), "setup time"),
    (("job", "operator_attendance"), "operator attendance"),
])
def test_missing_inputs_are_refused_not_defaulted(geo, path, label):
    rates, job = copy.deepcopy(RATES), copy.deepcopy(JOB)
    d = {"rates": rates, "job": job}[path[0]]
    for k in path[1:-1]:
        d = d[k]
    del d[path[-1]]
    with pytest.raises(CostingError, match=label):
        estimate(geo, rates, job, today=TODAY)


def test_unsourced_or_undated_numbers_are_refused(geo):
    rates = copy.deepcopy(RATES)
    rates["labour_rate_per_hour"] = {"value": 300.0, "unit": "INR/h", "source": "", "as_of": "2026-10-01"}
    with pytest.raises(CostingError, match="source"):
        estimate(geo, rates, JOB, today=TODAY)


def test_stale_inputs_warn(geo):
    rates = copy.deepcopy(RATES)
    rates["materials"]["AL6061-T6"]["price_per_kg"]["as_of"] = "2026-01-01"
    e = estimate(geo, rates, JOB, today=TODAY)
    assert any("price_per_kg" in w and "days old" in w for w in e.warnings)


def test_impossible_geometry_and_fractions_are_errors(geo):
    big = Geometry(1e9, geo.size_mm, "x", "y")
    with pytest.raises(CostingError, match="exceeds its stock"):
        estimate(big, RATES, JOB, today=TODAY)
    rates = copy.deepcopy(RATES)
    rates["scrap_fraction"] = S(1.0, "1")
    with pytest.raises(CostingError, match="fraction"):
        estimate(geo, rates, JOB, today=TODAY)


def test_margin_only_when_supplied(geo):
    rates = copy.deepcopy(RATES)
    rates["margin_fraction"] = S(0.15, "1", "commercial policy")
    e = estimate(geo, rates, JOB, today=TODAY)
    assert e.price_per_part == pytest.approx(e.cost_per_part * 1.15)


def test_reports_reopen_identically(geo, tmp_path):
    e = estimate(geo, RATES, JOB, today=TODAY)
    files = write(e, tmp_path, "plate")
    assert all(ok for _, ok, _ in verify(e, files))
