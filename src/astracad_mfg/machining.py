"""Machining calculations: deterministic, unit-explicit, and traceable.

Every calculation returns a `Calculation`: the inputs with units, the equation as text, the
assumptions, the outputs with units, and validity findings. Nothing is defaulted. In particular no
material data is bundled: cutting-force constants (kc1.1, mc) must be supplied with their source,
because they depend on the material lot, heat treatment, tool geometry and coating, and a typical
value quietly used would be an invented engineering fact.

Equations (public engineering knowledge; derivations are kinematic unless named):

  spindle speed        n  = 1000 vc / (pi D)                       [rpm]   vc m/min, D mm
  cutting speed        vc = pi D n / 1000                          [m/min]
  table feed (milling) vf = fz z n                                 [mm/min]
  feed per rev         vf = f n  (turning, drilling)               [mm/min]
  max chip thickness   hex = fz sin(kr) * k_ae,
                       k_ae = 2 sqrt(ae/D (1 - ae/D)) if ae < D/2, else 1   (radial chip thinning)
  removal rate         Q  = ap ae vf / 1000 (milling)              [cm^3/min]
                       Q  = vc ap f (turning)                      [cm^3/min]
                       Q  = (pi D^2 / 4) vf / 1000 (drilling)      [cm^3/min]
  specific force       kc = kc1.1 h^-mc  (Kienzle, 1952; h in mm, kc1.1 at h = 1 mm)  [N/mm^2]
  cutting power        Pc = Q kc / 60000                           [kW]    (N/mm^2 = J/cm^3)
  spindle power        Ps = Pc / eta
  torque               M  = 9549.3 Pc / n                          [N m]
  turning roughness    Rt = f^2 / (8 r)  ;  Ra ~ f^2 / (32 r)      [mm -> um x 1000]
                       (ideal geometric profile of a round nose; real Ra is higher: built-up edge,
                        vibration, tool wear and material side-flow are not in the model)
  ball-end scallop     h  = R - sqrt(R^2 - (ae/2)^2) ; ae = 2 sqrt(2 R h - h^2)
  ISO metric thread    (ISO 68-1 basic profile, H = 0.866025 P)
                       d2 = d - 0.649519 P ; D1 = d - 1.082532 P ; d3 = d - 1.226869 P
  thread engagement    % = (d - drill) / (1.299038 P) * 100  (percentage-of-thread convention:
                       100 % = twice the basic external thread height 0.649519 P)
  unit conversions     1 in = 25.4 mm exactly ; 1 ft = 0.3048 m exactly
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

FT_PER_M = 1 / 0.3048
MM_PER_IN = 25.4


class CalcError(ValueError):
    """An input is missing, non-physical or outside the model's range. The message says which."""


@dataclass
class Calculation:
    name: str
    equation: str
    inputs: dict[str, tuple[float, str]]
    outputs: dict[str, tuple[float, str]]
    assumptions: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)      # validity notes; "WARNING:" prefix for limits
    sources: list[str] = field(default_factory=list)       # user-supplied data sources (kc, eta ...)
    provenance: str = "CALCULATED"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["inputs"] = {k: {"value": v, "unit": u} for k, (v, u) in self.inputs.items()}
        d["outputs"] = {k: {"value": v, "unit": u} for k, (v, u) in self.outputs.items()}
        return d

    def out(self, key: str) -> float:
        return self.outputs[key][0]


def _pos(name: str, v, unit: str, allow_zero: bool = False) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise CalcError(f"{name} is required ({unit})") from None
    if not math.isfinite(x) or x < 0 or (x == 0 and not allow_zero):
        raise CalcError(f"{name} must be a {'non-negative' if allow_zero else 'positive'} finite number ({unit})")
    return x


# ---- speeds and feeds -------------------------------------------------------------------------

def spindle_speed(vc_m_min, diameter_mm) -> Calculation:
    vc, d = _pos("cutting speed vc", vc_m_min, "m/min"), _pos("tool/work diameter D", diameter_mm, "mm")
    n = 1000 * vc / (math.pi * d)
    return Calculation("spindle speed from cutting speed", "n = 1000 vc / (pi D)",
                       {"vc": (vc, "m/min"), "D": (d, "mm")}, {"n": (n, "rpm")},
                       ["D is the effective cutting diameter (for a ball end at shallow depth it is smaller than the nominal)"])


def cutting_speed(n_rpm, diameter_mm) -> Calculation:
    n, d = _pos("spindle speed n", n_rpm, "rpm"), _pos("diameter D", diameter_mm, "mm")
    vc = math.pi * d * n / 1000
    return Calculation("cutting speed from spindle speed", "vc = pi D n / 1000",
                       {"n": (n, "rpm"), "D": (d, "mm")}, {"vc": (vc, "m/min"), "vc_sfm": (vc * FT_PER_M, "ft/min")})


def milling_feed(fz_mm, teeth, n_rpm) -> Calculation:
    fz, n = _pos("feed per tooth fz", fz_mm, "mm"), _pos("spindle speed n", n_rpm, "rpm")
    z = _pos("number of teeth z", teeth, "-")
    if z != int(z):
        raise CalcError("number of teeth z must be a whole number")
    vf = fz * z * n
    return Calculation("milling table feed", "vf = fz z n", {"fz": (fz, "mm"), "z": (z, "-"), "n": (n, "rpm")},
                       {"vf": (vf, "mm/min"), "vf_ipm": (vf / MM_PER_IN, "in/min")})


def chip_thinning(fz_mm, ae_mm, diameter_mm, lead_angle_deg=90.0) -> Calculation:
    """Maximum chip thickness at a radial engagement, and the feed per tooth that restores a target."""
    fz, ae, d = _pos("feed per tooth fz", fz_mm, "mm"), _pos("radial engagement ae", ae_mm, "mm"), _pos("cutter diameter D", diameter_mm, "mm")
    kr = _pos("entering (lead) angle kr", lead_angle_deg, "deg")
    if ae > d:
        raise CalcError("radial engagement ae cannot exceed the cutter diameter D")
    if kr > 90:
        raise CalcError("entering angle kr is at most 90 deg (square shoulder)")
    r = ae / d
    k_ae = 2 * math.sqrt(r * (1 - r)) if r < 0.5 else 1.0
    s = math.sin(math.radians(kr))
    hex_ = fz * s * k_ae
    fz_for_same = fz / (s * k_ae)
    calc = Calculation("radial / lead-angle chip thinning", "hex = fz sin(kr) k_ae ; k_ae = 2 sqrt(ae/D (1 - ae/D)) for ae < D/2",
                       {"fz": (fz, "mm"), "ae": (ae, "mm"), "D": (d, "mm"), "kr": (kr, "deg")},
                       {"hex": (hex_, "mm"), "thinning_factor": (s * k_ae, "-"),
                        "fz_for_hex_equal_fz": (fz_for_same, "mm")},
                       ["straight-flute geometry; helix angle and edge rounding are not modelled"])
    if fz_for_same > 3 * fz:
        calc.findings.append("WARNING: compensated feed is more than 3x the nominal; check the tool maker's limit")
    return calc


def removal_rate(process: str, *, ap_mm=None, ae_mm=None, vf_mm_min=None, vc_m_min=None, f_mm_rev=None,
                 diameter_mm=None) -> Calculation:
    if process == "milling":
        ap, ae, vf = _pos("axial depth ap", ap_mm, "mm"), _pos("radial width ae", ae_mm, "mm"), _pos("table feed vf", vf_mm_min, "mm/min")
        q = ap * ae * vf / 1000
        return Calculation("material removal rate - milling", "Q = ap ae vf / 1000",
                           {"ap": (ap, "mm"), "ae": (ae, "mm"), "vf": (vf, "mm/min")}, {"Q": (q, "cm3/min")})
    if process == "turning":
        vc, ap, f = _pos("cutting speed vc", vc_m_min, "m/min"), _pos("depth of cut ap", ap_mm, "mm"), _pos("feed f", f_mm_rev, "mm/rev")
        q = vc * ap * f
        return Calculation("material removal rate - turning", "Q = vc ap f",
                           {"vc": (vc, "m/min"), "ap": (ap, "mm"), "f": (f, "mm/rev")}, {"Q": (q, "cm3/min")},
                           ["ap small against the workpiece radius (vc taken at the cut)"])
    if process == "drilling":
        d, vf = _pos("drill diameter D", diameter_mm, "mm"), _pos("feed rate vf", vf_mm_min, "mm/min")
        q = math.pi * d * d / 4 * vf / 1000
        return Calculation("material removal rate - drilling (solid)", "Q = (pi D^2 / 4) vf / 1000",
                           {"D": (d, "mm"), "vf": (vf, "mm/min")}, {"Q": (q, "cm3/min")})
    raise CalcError("process must be milling, turning or drilling")


def cutting_power(q_cm3_min, *, kc_n_mm2=None, kc11_n_mm2=None, mc=None, hm_mm=None, n_rpm=None,
                  efficiency=None, source: str = "") -> Calculation:
    """Power from removal rate and specific cutting force: kc given, or from Kienzle constants at the
    mean chip thickness. The constants' source is required - they are material data."""
    q = _pos("removal rate Q", q_cm3_min, "cm3/min")
    if not str(source).strip():
        raise CalcError("state the source of the cutting-force data (handbook/tool maker/test, with date)")
    inputs = {"Q": (q, "cm3/min")}
    assumptions = ["kc is constant over the cut (mean chip thickness)"]
    if kc_n_mm2 is not None:
        kc = _pos("specific cutting force kc", kc_n_mm2, "N/mm2")
        inputs["kc"] = (kc, "N/mm2")
        eq = "Pc = Q kc / 60000"
    else:
        kc11 = _pos("kc1.1", kc11_n_mm2, "N/mm2")
        m = _pos("mc", mc, "-", allow_zero=True)
        h = _pos("mean chip thickness hm", hm_mm, "mm")
        if not (0 <= m < 1):
            raise CalcError("mc is an exponent between 0 and 1")
        kc = kc11 * h ** (-m)
        inputs.update({"kc1.1": (kc11, "N/mm2"), "mc": (m, "-"), "hm": (h, "mm")})
        eq = "kc = kc1.1 hm^-mc (Kienzle) ; Pc = Q kc / 60000"
        assumptions.append("Kienzle model: kc1.1 and mc measured for this material and tool geometry")
    pc = q * kc / 60000
    outputs = {"kc": (kc, "N/mm2"), "Pc": (pc, "kW")}
    findings = []
    if efficiency is not None:
        eta = _pos("machine efficiency eta", efficiency, "-")
        if eta > 1:
            raise CalcError("efficiency is a fraction (0..1]")
        inputs["eta"] = (eta, "-")
        outputs["P_spindle"] = (pc / eta, "kW")
        eq += " ; Ps = Pc / eta"
    if n_rpm is not None:
        n = _pos("spindle speed n", n_rpm, "rpm")
        inputs["n"] = (n, "rpm")
        outputs["torque"] = (9549.296586 * pc / n, "N m")
        eq += " ; M = 9549.3 Pc / n"
    return Calculation("cutting power", eq, inputs, outputs, assumptions, findings, [str(source).strip()])


# ---- surface finish ---------------------------------------------------------------------------

def turning_roughness(f_mm_rev, nose_radius_mm) -> Calculation:
    f, r = _pos("feed f", f_mm_rev, "mm/rev"), _pos("nose radius r", nose_radius_mm, "mm")
    rt = f * f / (8 * r) * 1000
    ra = f * f / (32 * r) * 1000
    c = Calculation("theoretical turning roughness", "Rt = f^2 / (8 r) ; Ra ~ f^2 / (32 r)",
                    {"f": (f, "mm/rev"), "r": (r, "mm")}, {"Rt": (rt, "um"), "Ra": (ra, "um")},
                    ["ideal geometric profile of a round nose; achieved Ra is normally higher"])
    if f > r:
        c.findings.append("WARNING: feed exceeds the nose radius; the round-nose profile model does not hold")
    return c


def turning_feed_for_ra(ra_um, nose_radius_mm) -> Calculation:
    ra, r = _pos("target Ra", ra_um, "um"), _pos("nose radius r", nose_radius_mm, "mm")
    f = math.sqrt(32 * r * ra / 1000)
    c = Calculation("maximum turning feed for a target Ra", "f = sqrt(32 r Ra)",
                    {"Ra": (ra, "um"), "r": (r, "mm")}, {"f_max": (f, "mm/rev")},
                    ["geometric upper bound: use a lower feed in practice (the real profile is rougher)"])
    if f > r:
        c.findings.append("WARNING: required feed exceeds the nose radius; the model does not hold there")
    return c


def scallop_height(ball_radius_mm, stepover_mm) -> Calculation:
    r, ae = _pos("ball radius R", ball_radius_mm, "mm"), _pos("stepover ae", stepover_mm, "mm")
    if ae >= 2 * r:
        raise CalcError("stepover must be smaller than the ball diameter")
    h = r - math.sqrt(r * r - (ae / 2) ** 2)
    return Calculation("ball-end scallop height", "h = R - sqrt(R^2 - (ae/2)^2)",
                       {"R": (r, "mm"), "ae": (ae, "mm")}, {"h": (h, "mm"), "h_um": (h * 1000, "um")},
                       ["flat surface normal to the tool axis"])


def stepover_for_scallop(ball_radius_mm, scallop_mm) -> Calculation:
    r, h = _pos("ball radius R", ball_radius_mm, "mm"), _pos("scallop height h", scallop_mm, "mm")
    if h >= r:
        raise CalcError("scallop height must be smaller than the ball radius")
    ae = 2 * math.sqrt(2 * r * h - h * h)
    return Calculation("stepover for a scallop height", "ae = 2 sqrt(2 R h - h^2)",
                       {"R": (r, "mm"), "h": (h, "mm")}, {"ae": (ae, "mm")}, ["flat surface normal to the tool axis"])


# ---- threads ------------------------------------------------------------------------------------

def iso_metric_thread(d_mm, pitch_mm) -> Calculation:
    d, p = _pos("nominal diameter d", d_mm, "mm"), _pos("pitch P", pitch_mm, "mm")
    if p >= d / 2:
        raise CalcError("pitch must be well below the diameter")
    out = {"H": (0.8660254 * p, "mm"), "d2_pitch_diameter": (d - 0.649519 * p, "mm"),
           "D1_internal_minor": (d - 1.082532 * p, "mm"), "d3_external_minor": (d - 1.226869 * p, "mm")}
    return Calculation("ISO metric thread - basic dimensions", "ISO 68-1 basic profile: H = 0.866025 P ; d2 = d - 0.649519 P ; "
                       "D1 = d - 1.082532 P ; d3 = d - 1.226869 P", {"d": (d, "mm"), "P": (p, "mm")}, out,
                       ["basic (zero-allowance) dimensions; tolerance classes (e.g. 6H/6g) are not applied"])


def tap_drill(d_mm, pitch_mm, engagement_pct=None, drill_mm=None) -> Calculation:
    """Either the drill for a thread percentage, or the thread percentage a drill gives."""
    d, p = _pos("nominal diameter d", d_mm, "mm"), _pos("pitch P", pitch_mm, "mm")
    if (engagement_pct is None) == (drill_mm is None):
        raise CalcError("give either the thread percentage or the drill diameter")
    if engagement_pct is not None:
        pct = _pos("thread percentage", engagement_pct, "%")
        if pct > 100:
            raise CalcError("thread percentage is at most 100")
        drill = d - 1.299038 * p * pct / 100
        inputs = {"d": (d, "mm"), "P": (p, "mm"), "percent": (pct, "%")}
        outputs = {"drill": (drill, "mm")}
    else:
        drill = _pos("drill diameter", drill_mm, "mm")
        if drill >= d:
            raise CalcError("drill must be smaller than the thread diameter")
        pct = (d - drill) / (1.299038 * p) * 100
        inputs = {"d": (d, "mm"), "P": (p, "mm"), "drill": (drill, "mm")}
        outputs = {"percent": (pct, "%")}
    c = Calculation("tap drill / thread percentage", "% = (d - drill) / (1.299038 P) x 100",
                    inputs, outputs, ["percentage-of-thread convention (100 % = 2 x 0.649519 P)",
                                      "the tapped minor diameter must still meet the thread class's D1 limits"])
    d1 = d - 1.082532 * p
    if drill < d1 - 1e-9:
        c.findings.append(f"WARNING: drill {drill:.3f} mm is below the basic minor diameter D1 {d1:.3f} mm: "
                          "the tap would have to cut the minor diameter")
    return c


# ---- registry for the workspace ------------------------------------------------------------------

REGISTRY = {
    "spindle_speed": (spindle_speed, [("vc_m_min", "Cutting speed vc", "m/min"), ("diameter_mm", "Diameter D", "mm")]),
    "cutting_speed": (cutting_speed, [("n_rpm", "Spindle speed n", "rpm"), ("diameter_mm", "Diameter D", "mm")]),
    "milling_feed": (milling_feed, [("fz_mm", "Feed per tooth fz", "mm"), ("teeth", "Teeth z", "-"), ("n_rpm", "Spindle speed n", "rpm")]),
    "chip_thinning": (chip_thinning, [("fz_mm", "Feed per tooth fz", "mm"), ("ae_mm", "Radial engagement ae", "mm"),
                                      ("diameter_mm", "Cutter diameter D", "mm"), ("lead_angle_deg", "Entering angle kr", "deg")]),
    "removal_rate": (removal_rate, [("process", "Process (milling / turning / drilling)", ""), ("ap_mm", "Depth ap", "mm"),
                                    ("ae_mm", "Width ae (milling)", "mm"), ("vf_mm_min", "Feed rate vf", "mm/min"),
                                    ("vc_m_min", "Cutting speed vc (turning)", "m/min"), ("f_mm_rev", "Feed f (turning)", "mm/rev"),
                                    ("diameter_mm", "Drill diameter D (drilling)", "mm")]),
    "cutting_power": (cutting_power, [("q_cm3_min", "Removal rate Q", "cm3/min"), ("kc_n_mm2", "kc (or kc1.1 + mc + hm)", "N/mm2"),
                                      ("kc11_n_mm2", "kc1.1", "N/mm2"), ("mc", "mc", "-"), ("hm_mm", "Mean chip thickness hm", "mm"),
                                      ("n_rpm", "Spindle speed n (torque)", "rpm"), ("efficiency", "Machine efficiency", "0-1"),
                                      ("source", "Source of the force data", "")]),
    "turning_roughness": (turning_roughness, [("f_mm_rev", "Feed f", "mm/rev"), ("nose_radius_mm", "Nose radius r", "mm")]),
    "turning_feed_for_ra": (turning_feed_for_ra, [("ra_um", "Target Ra", "um"), ("nose_radius_mm", "Nose radius r", "mm")]),
    "scallop_height": (scallop_height, [("ball_radius_mm", "Ball radius R", "mm"), ("stepover_mm", "Stepover ae", "mm")]),
    "stepover_for_scallop": (stepover_for_scallop, [("ball_radius_mm", "Ball radius R", "mm"), ("scallop_mm", "Scallop height h", "mm")]),
    "iso_metric_thread": (iso_metric_thread, [("d_mm", "Nominal diameter d", "mm"), ("pitch_mm", "Pitch P", "mm")]),
    "tap_drill": (tap_drill, [("d_mm", "Nominal diameter d", "mm"), ("pitch_mm", "Pitch P", "mm"),
                              ("engagement_pct", "Thread % (or drill)", "%"), ("drill_mm", "Drill diameter", "mm")]),
}


def run(name: str, params: dict) -> Calculation:
    if name not in REGISTRY:
        raise CalcError(f"unknown calculation {name!r}")
    fn, fields = REGISTRY[name]
    kw = {k: params[k] for k, _, _ in fields if params.get(k) not in (None, "")}
    import inspect
    required = [p.name for p in inspect.signature(fn).parameters.values()
                if p.default is inspect.Parameter.empty and p.kind is not p.KEYWORD_ONLY]
    missing = [label for k, label, unit in fields if k in required and k not in kw]
    if missing:
        raise CalcError("required: " + ", ".join(missing))
    if name == "removal_rate":
        proc = kw.pop("process", None)
        return fn(proc, **kw)
    if name == "cutting_power":
        q = kw.pop("q_cm3_min", None)
        return fn(q, **kw)
    return fn(**kw)
