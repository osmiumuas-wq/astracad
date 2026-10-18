"""Two kernels on one spec: OpenCascade's build of the spec against SOLIDWORKS's own measurements (replayed from
real SOLIDWORKS 2026 runs in tests/fixtures/step), inside the SOLIDWORKS adapter's build report."""
import json
from pathlib import Path

import pytest

pytest.importorskip("OCP")

import astracad_geom.compare as cmp  # noqa: E402
from astracad_cad.adapters import SolidWorksAdapter  # noqa: E402
from astracad_cad.contract import Op, OpStatus  # noqa: E402
from astracad_cad.step_check import read_step  # noqa: E402

from test_cad_contract import MAC, fake_mac, prism_spec, sw_probe  # noqa: E402

STEP = Path(__file__).parent / "fixtures" / "step"
REAL = json.loads((STEP / "prism.report.json").read_text())["measured"]      # SOLIDWORKS 2026, 60 x 40 x 20


def outcomes(checks):
    return {c["name"]: c["outcome"] for c in checks}


def test_a_real_solidworks_prism_agrees_with_opencascade():
    box = read_step(STEP / "prism.step")
    checks, data = cmp.compare(prism_spec(), REAL, box.bbox_mm, box.bbox_approximate)
    assert outcomes(checks) == {"occ:build": "passed", "occ:volume_mm3": "passed", "occ:surface_area_mm2": "passed",
                                "occ:center_of_mass_mm": "passed", "occ:solid_bodies": "passed",
                                "occ:step_bbox_mm": "passed"}
    assert data["measured"]["volume_mm3"] == pytest.approx(48000, rel=1e-12)


def test_a_measurement_that_differs_fails():
    wrong = {**REAL, "volume_mm3": REAL["volume_mm3"] * 1.001}
    checks, _ = cmp.compare(prism_spec(), wrong)
    bad = [c for c in checks if c["outcome"] == "failed"]
    assert [c["name"] for c in bad] == ["occ:volume_mm3"] and "exceeds 1e-06" in bad[0]["why"]


def test_through_all_has_solidworks_measured_band_and_no_more():
    spec = {"name": "t", "units": "mm", "features": [
        {"$op": "sketch.rectangle", "id": "s", "sketched_on": "front", "args": {"width": 60, "height": 40}},
        {"$op": "feature.extrude", "id": "e", "args": {"depth": 20}},
        {"$op": "feature.hole", "id": "h", "sketched_on": "front",
         "args": {"diameter": 10, "through_all": True, "center": [30, 20]}}]}
    exact = cmp.compare(spec, {})[1]["measured"]
    near = {"volume_mm3": exact["volume_mm3"] * (1 + 5e-6), "surface_area_mm2": exact["surface_area_mm2"],
            "center_of_mass_mm": exact["center_of_mass_mm"], "solid_bodies": 1}
    assert outcomes(cmp.compare(spec, near)[0])["occ:volume_mm3"] == "passed"
    far = {**near, "volume_mm3": exact["volume_mm3"] * (1 + 2e-5)}
    assert outcomes(cmp.compare(spec, far)[0])["occ:volume_mm3"] == "failed"


def test_without_opencascade_nothing_is_claimed(monkeypatch):
    monkeypatch.setattr(cmp, "available", lambda: False)
    (c,), data = cmp.compare(prism_spec(), REAL)
    assert c["outcome"] == "not_verifiable" and data == {}


def test_what_the_second_kernel_cannot_build_yet_is_not_verifiable():
    spec = {**prism_spec(), "language": 2,
            "features": prism_spec()["features"] + [{"$op": "feature.shell", "id": "x", "args": {"thickness": 2}}]}
    (c,), _ = cmp.compare(spec, REAL)
    assert c["outcome"] == "not_verifiable" and "not built by the second kernel" in c["why"]


def test_a_spec_the_second_kernel_refuses_is_a_failure():
    spec = {**prism_spec(), "features": prism_spec()["features"][:1]}          # a sketch nothing uses
    (c,), _ = cmp.compare(spec, REAL)
    assert c["outcome"] == "failed" and "never used" in c["actual"]


def test_the_adapter_report_carries_both_kernels(tmp_path):
    a = SolidWorksAdapter(sw_probe(fake_mac("prism")), mac=MAC)
    r = a.execute(Op.CREATE_FEATURE, {"spec": prism_spec(), "out_dir": str(tmp_path)})
    assert r.status is OpStatus.SUCCEEDED, [c for c in r.checks if c.outcome != "passed"]
    occ = {c.name: c.outcome for c in r.checks if c.name.startswith("occ:")}
    assert occ["occ:volume_mm3"] == occ["occ:center_of_mass_mm"] == occ["occ:step_bbox_mm"] == "passed"
    assert r.data["occ"]["measured"]["volume_mm3"] == pytest.approx(48000)


def test_the_adapter_does_not_accept_a_build_the_kernels_disagree_on(tmp_path):
    # SOLIDWORKS's report is a prism with a hole; the spec asked for a plain prism
    a = SolidWorksAdapter(sw_probe(fake_mac("prism_hole")), mac=MAC)
    r = a.execute(Op.CREATE_FEATURE, {"spec": prism_spec("prism_hole"), "out_dir": str(tmp_path)})
    assert r.status is OpStatus.EXECUTED_UNVERIFIED
    assert {"occ:volume_mm3", "occ:surface_area_mm2"} <= {c.name for c in r.checks if c.outcome == "failed"}
