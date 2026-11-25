"""Assemblies in OpenCascade (M2): parts placed by frames, their interfaces carried along, mates checked, and every
overlap between components found (docs/engineering/assembly-spec.md).

Nothing is solved here: a template computes where each component goes (kinematics, fits) and the assembly is then
checked - each recorded mate must hold geometrically, and two components may overlap only if the spec declares them
a contact (a fit). Everything else that overlaps is an interference.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from OCP.BRep import BRep_Builder
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
from OCP.TopoDS import TopoDS_Compound
from OCP.gp import gp_Trsf

from . import frames as F
from .occ_build import SpecError, bbox, build

LIN_TOL = 1e-6
ANG_TOL = 1e-9
MATES = {"concentric", "coaxial", "coincident", "plane_distance", "angle"}


@dataclass
class Placed:
    id: str
    part: str
    shape: Any
    origin: F.Vec
    x: F.Vec
    y: F.Vec
    z: F.Vec
    interfaces: dict[str, dict] = field(default_factory=dict)
    volume: float = 0.0


@dataclass
class AssemblyResult:
    name: str
    components: dict[str, Placed]
    checks: list[dict]
    parts: dict[str, Any]
    compound: Any
    log: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if c["outcome"] == "failed"]


def _frame(spec, what: str):
    if not isinstance(spec, dict):
        raise SpecError(f"{what}: frame must be {{origin, x, z}}")
    o = spec.get("origin")
    if not (isinstance(o, (list, tuple)) and len(o) == 3):
        raise SpecError(f"{what}: frame origin must be [x, y, z]")
    try:
        x, z = F.unit(spec.get("x"), f"{what}.x"), F.unit(spec.get("z"), f"{what}.z")
    except ValueError as ex:
        raise SpecError(str(ex)) from None
    if abs(F.dot(x, z)) > 1e-9:
        raise SpecError(f"{what}: x and z must be perpendicular")
    return (float(o[0]), float(o[1]), float(o[2])), x, F.cross(z, x), z


def _place(origin, x, y, z, p):
    return F.add(origin, F.add(F.add(F.mul(x, p[0]), F.mul(y, p[1])), F.mul(z, p[2])))


def _rotate(x, y, z, v):
    return F.add(F.add(F.mul(x, v[0]), F.mul(y, v[1])), F.mul(z, v[2]))


def _volume(shape) -> float:
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def _c(name, outcome, expected, actual, why=None) -> dict:
    return {"name": name, "outcome": outcome, "expected": str(expected)[:200], "actual": str(actual)[:200], "why": why}


def _boxes_touch(a, b, pad) -> bool:
    return all(a[i] <= b[i + 3] + pad and b[i] <= a[i + 3] + pad for i in range(3))


def check_mate(m: dict, comps: dict[str, Placed]) -> dict:
    kind = m.get("type")
    try:
        (ca, ia), (cb, ib) = m["a"], m["b"]
        fa, fb = comps[ca].interfaces[ia], comps[cb].interfaces[ib]
    except (KeyError, TypeError, ValueError):
        raise SpecError(f"mate {m}: a and b must be [component, interface] that exist") from None
    name = f"mate:{kind}:{ca}.{ia}~{cb}.{ib}"
    za, zb = fa["z"], fb["z"]
    d = F.sub(fb["origin"], fa["origin"])
    parallel = abs(abs(F.dot(za, zb)) - 1) <= ANG_TOL
    off_axis = F.norm(F.cross(d, za))                 # distance of b's origin from a's axis line
    if kind in ("concentric", "coaxial"):
        ok = parallel and off_axis <= LIN_TOL and (kind == "concentric" or F.dot(za, zb) > 0)
        return _c(name, "passed" if ok else "failed", "axes on one line" + (", same direction" if kind == "coaxial" else ""),
                  f"axis angle {math.degrees(math.acos(max(-1.0, min(1.0, abs(F.dot(za, zb)))))):.3g} deg, "
                  f"offset {off_axis:.3g} mm")
    if kind == "coincident":
        gap = F.norm(d)
        return _c(name, "passed" if gap <= LIN_TOL else "failed", "origins coincide", f"{gap:.3g} mm apart")
    if kind == "plane_distance":
        want = float(m.get("value", 0.0))
        got = abs(F.dot(d, za))
        ok = parallel and abs(got - want) <= LIN_TOL
        return _c(name, "passed" if ok else "failed", f"parallel planes {want:g} mm apart",
                  f"{got:.6g} mm, {'parallel' if parallel else 'not parallel'}")
    if kind == "angle":
        want = float(m.get("value"))
        got = math.degrees(math.acos(max(-1.0, min(1.0, F.dot(za, zb)))))
        return _c(name, "passed" if abs(got - want) <= 1e-7 else "failed", f"{want:g} deg", f"{got:.9g} deg")
    raise SpecError(f"mate type must be one of {sorted(MATES)}, not {kind!r}")


def build_assembly(asm: dict, part_builder=build) -> AssemblyResult:
    if not isinstance(asm, dict) or not isinstance(asm.get("components"), list) or not asm["components"]:
        raise SpecError("an assembly needs a list of components")
    if str(asm.get("units", "mm")).lower() != "mm":
        raise SpecError("only mm assemblies")
    parts_spec = asm.get("parts") or {}
    built: dict[str, Any] = {}
    comps: dict[str, Placed] = {}
    log: list[str] = []
    for c in asm["components"]:
        cid, pid = str(c.get("id") or ""), str(c.get("part") or "")
        if not cid or cid in comps:
            raise SpecError(f"component id {cid!r} missing or used twice")
        if pid not in parts_spec:
            raise SpecError(f"{cid}: unknown part {pid!r}")
        if pid not in built:
            ps = parts_spec[pid].get("spec") if isinstance(parts_spec[pid], dict) else None
            if not isinstance(ps, dict):
                raise SpecError(f"part {pid}: needs a spec")
            built[pid] = part_builder(ps)
        origin, x, y, z = _frame(c.get("frame"), cid)
        t = gp_Trsf()
        t.SetValues(x[0], y[0], z[0], origin[0], x[1], y[1], z[1], origin[1], x[2], y[2], z[2], origin[2])
        shape = BRepBuilderAPI_Transform(built[pid].shape, t, True).Shape()
        ifaces = {}
        for name, fr in ((parts_spec[pid].get("spec") or {}).get("interfaces") or {}).items():
            o, ix, _iy, iz = _frame(fr, f"{pid}.{name}")
            ifaces[name] = {"origin": _place(origin, x, y, z, o), "z": _rotate(x, y, z, iz), "x": _rotate(x, y, z, ix)}
        comps[cid] = Placed(cid, pid, shape, origin, x, y, z, ifaces, _volume(shape))
    log.append(f"{len(comps)} component(s) of {len(built)} part(s) placed")

    checks: list[dict] = []
    for m in asm.get("mates") or []:
        checks.append(check_mate(m, comps))

    # a declared contact must touch (distance 0) and may overlap only by its declared amount (0 unless a press fit)
    contacts: dict[frozenset, float] = {}
    for entry in asm.get("contacts") or []:
        pair, limit = (entry.get("pair"), float(entry.get("max_overlap_mm3", 0.0))) if isinstance(entry, dict)             else (entry, 0.0)
        if not (isinstance(pair, list) and len(pair) == 2 and all(p in comps for p in pair) and limit >= 0):
            raise SpecError(f"contact {entry!r} must name two components (and a max_overlap_mm3 >= 0)")
        contacts[frozenset(pair)] = limit
    ids = list(comps)
    boxes = {i: bbox(comps[i].shape) for i in ids}
    clashes = 0
    for n, i in enumerate(ids):
        for j in ids[n + 1:]:
            if not _boxes_touch(boxes[i], boxes[j], LIN_TOL):
                continue
            op = BRepAlgoAPI_Common(comps[i].shape, comps[j].shape)
            v = _volume(op.Shape()) if op.IsDone() else float("nan")
            tol = max(1e-6, 1e-9 * min(comps[i].volume, comps[j].volume))
            key = frozenset((i, j))
            if not math.isfinite(v):
                checks.append(_c(f"interference:{i}~{j}", "not_verifiable", "overlap computed", "boolean failed"))
            elif key in contacts:
                continue                                  # judged below, with the touch test
            elif v > tol:
                clashes += 1
                checks.append(_c(f"interference:{i}~{j}", "failed", "no overlap", f"{v:.6g} mm3 overlap"))
    for key, limit in contacts.items():
        i, j = sorted(key)
        dist = BRepExtrema_DistShapeShape(comps[i].shape, comps[j].shape)
        gap = dist.Value() if dist.IsDone() else float("nan")
        op = BRepAlgoAPI_Common(comps[i].shape, comps[j].shape)
        v = _volume(op.Shape()) if op.IsDone() else float("nan")
        tol = max(1e-6, 1e-9 * min(comps[i].volume, comps[j].volume))
        ok = gap <= LIN_TOL and v <= max(tol, limit)
        if not ok and v > max(tol, limit):
            clashes += 1
        checks.append(_c(f"contact:{i}~{j}", "passed" if ok else "failed",
                         f"touching, overlap <= {limit:g} mm3", f"gap {gap:.3g} mm, overlap {v:.6g} mm3",
                         None if ok else ("they do not touch" if gap > LIN_TOL else "they overlap more than declared")))
    checks.append(_c("interference:all", "passed" if clashes == 0 else "failed", "no undeclared overlap",
                     f"{clashes} pair(s) overlap"))

    comp = TopoDS_Compound()
    bld = BRep_Builder()
    bld.MakeCompound(comp)
    for p in comps.values():
        bld.Add(comp, p.shape)
    return AssemblyResult(str(asm.get("assembly") or "assembly"), comps, checks, built, comp, log)


def export_step(result: AssemblyResult, path) -> None:
    w = STEPControl_Writer()
    w.Transfer(result.compound, STEPControl_AsIs)
    if w.Write(str(path)) != IFSelect_RetDone:
        raise RuntimeError(f"STEP export failed: {path}")
