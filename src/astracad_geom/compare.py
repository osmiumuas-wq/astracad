"""Two kernels, one spec: SOLIDWORKS's measurements against OpenCascade's own build of the same spec.

OpenCascade never sees anything SOLIDWORKS produced - it builds from the spec. Agreement within the tolerance of the
part's verification class (docs/engineering/part-language-v2.md section 6) is evidence that SOLIDWORKS built what the
spec says. A spec the second kernel cannot build yet is "not verifiable" with the reason; a spec it refuses as invalid
while SOLIDWORKS built it is a failure - the kernels disagree about the language.
"""
from __future__ import annotations

import math

from . import available

CLASS_TOL = {"E": 1e-6, "B": 1e-4, "F": 1e-3}
# SOLIDWORKS resolves a through-all end condition inside its kernel rather than from a dimension: measured on this
# install up to 5.6e-6 relative (area) against the exact solid; mac's own analytic check uses 1e-5 for the same reason
THROUGH_ALL_TOL = 1e-5


def _through_all(spec: dict) -> bool:
    return any(isinstance(f, dict) and (f.get("args") or {}).get("through_all") for f in spec.get("features") or [])


def _c(name, outcome, expected, actual, why=None) -> dict:
    return {"name": name, "outcome": outcome, "expected": str(expected)[:200], "actual": str(actual)[:200], "why": why}


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def compare(spec: dict, measured: dict, step_bbox: list | None = None,
            step_bbox_approximate: bool = True) -> tuple[list[dict], dict]:
    """Checks (as dicts: name, outcome, expected, actual, why) and OpenCascade's own numbers for the report."""
    if not available():
        return [_c("occ:build", "not_verifiable", "an OpenCascade build of the spec", "OpenCascade not installed",
                   "install cadquery-ocp (docs/engineering/two-kernel-verification.md)")], {}
    from .occ_build import BuildError, NotSupported, QueryMismatch, SpecError, build
    from .props import measure
    try:
        built = build(spec)
    except NotSupported as ex:
        return [_c("occ:build", "not_verifiable", "an OpenCascade build of the spec", "not built", str(ex))], {}
    except SpecError as ex:
        return [_c("occ:build", "failed", "the spec is valid Part Language", f"refused: {ex}",
                   "SOLIDWORKS ran a spec the second kernel finds invalid")], {"refused": str(ex)}
    except QueryMismatch as ex:            # the language fixes what a query selects: a different count is a fault
        return [_c("occ:build", "failed", "the spec's edge/face queries hold", "count differs", str(ex))], {}
    except BuildError as ex:
        return [_c("occ:build", "not_verifiable", "an OpenCascade build of the spec",
                   "OpenCascade could not build it", str(ex))], {}
    m = measure(built.shape)
    cls = built.tolerance_class
    tol = CLASS_TOL.get(cls, 1e-6)
    if _through_all(spec) and tol < THROUGH_ALL_TOL:
        tol, cls = THROUGH_ALL_TOL, cls + " (through-all)"
    size = max(1.0, *m["size_mm"])
    checks = [_c("occ:build", "passed", "an OpenCascade build of the spec",
                 f"{m['solids']} solid(s), {m['faces']} faces, class {cls}")]

    for key in ("volume_mm3", "surface_area_mm2"):
        sw, occ = _num(measured.get(key)), m[key]
        if sw is None:
            checks.append(_c(f"occ:{key}", "not_verifiable", f"{occ:.6f} (OpenCascade)", "SOLIDWORKS gave no number"))
            continue
        ok = abs(sw - occ) <= tol * max(1.0, abs(occ))
        checks.append(_c(f"occ:{key}", "passed" if ok else "failed", f"{occ:.6f} (OpenCascade)",
                         f"{sw:.6f} (SOLIDWORKS)", None if ok else
                         f"relative difference {(sw - occ) / max(1.0, abs(occ)):+.2e} exceeds {tol:g} (class {cls})"))

    com = measured.get("center_of_mass_mm")
    if isinstance(com, list) and len(com) >= 3 and all(_num(x) is not None for x in com[:3]):
        d = max(abs(float(a) - b) for a, b in zip(com[:3], m["center_of_mass_mm"]))
        ok = d <= tol * size
        checks.append(_c("occ:center_of_mass_mm", "passed" if ok else "failed",
                         [round(x, 6) for x in m["center_of_mass_mm"]], [round(float(x), 6) for x in com[:3]],
                         None if ok else f"differs by {d:.3g} mm (allowed {tol * size:.3g})"))
    else:
        checks.append(_c("occ:center_of_mass_mm", "not_verifiable", m["center_of_mass_mm"], "SOLIDWORKS gave none"))

    bodies = measured.get("solid_bodies")
    if isinstance(bodies, int):
        checks.append(_c("occ:solid_bodies", "passed" if bodies == m["solids"] else "failed", m["solids"], bodies))

    if step_bbox and len(step_bbox) == 6 and not step_bbox_approximate:
        d = max(abs(float(a) - b) for a, b in zip(step_bbox, m["bbox_mm"]))
        ok = d <= max(1e-6, tol) * size
        checks.append(_c("occ:step_bbox_mm", "passed" if ok else "failed", [round(x, 6) for x in m["bbox_mm"]],
                         [round(float(x), 6) for x in step_bbox], None if ok else
                         f"the exported STEP's box differs from OpenCascade's by {d:.3g} mm"))
    return checks, {"measured": m, "class": cls, "log": built.log}
