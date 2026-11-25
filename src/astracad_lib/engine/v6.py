"""A 60-degree V6 engine core: crankshaft, connecting rods, pistons, piston pins and block (docs/engineering/v6-template.md).

Engineering-simplified on purpose - correct key dimensions, crank phasing, kinematics and fits; no casting drafts,
water jackets, oil galleries or real port shapes. Every number is either a parameter (with its source: given by the
user, or a stated default the user must accept) or derived here from the parameters by a named relation:

- crank radius r = stroke / 2; deck height D = r + rod length + compression height + deck clearance;
- crankpin phase for even firing: a cylinder on the bank whose axis is at angle b (from +Z toward +Y) fires at crank
  angle 120 k (k = its place in the firing order); its pin points along the bank axis then, so its phase is
  b + 120 k (mod 360) - for 1-2-3-4-5-6 on a 60-degree V that is six split pins 60 degrees apart;
- piston pin position on its bank axis by the slider-crank relation s = r cos t + sqrt(l^2 - r^2 sin^2 t), t the angle
  between the crankpin and the bank axis.

Coordinates: the crank axis is +X, +Z is up; the crank turns about +X (right hand). Bank R (cylinders 1, 3, 5) leans
toward +Y, bank L (2, 4, 6) toward -Y.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .. import spec as S

# defaults: a 3.5 L 60-degree V6 (dimensions of the Nissan VQ35 family, public data) - "assumed" until accepted
DEFAULTS = {"bore": 95.5, "stroke": 81.4, "rod_length": 144.2, "compression_height": 30.0, "bank_angle": 60.0,
            "firing_order": [1, 2, 3, 4, 5, 6], "main_journal_d": 64.0, "crankpin_d": 54.0, "piston_pin_d": 22.0,
            "rod_width": 22.0, "web_thickness": 18.0, "journal_width": 26.0, "deck_clearance": 0.5,
            "piston_clearance": 0.1, "skirt_length": 36.0, "rod_swing_clearance": 30.0, "crank_angle": 0.0}


@dataclass
class V6Params:
    bore: float = DEFAULTS["bore"]
    stroke: float = DEFAULTS["stroke"]
    rod_length: float = DEFAULTS["rod_length"]
    compression_height: float = DEFAULTS["compression_height"]
    bank_angle: float = DEFAULTS["bank_angle"]
    firing_order: list = field(default_factory=lambda: list(DEFAULTS["firing_order"]))
    main_journal_d: float = DEFAULTS["main_journal_d"]
    crankpin_d: float = DEFAULTS["crankpin_d"]
    piston_pin_d: float = DEFAULTS["piston_pin_d"]
    rod_width: float = DEFAULTS["rod_width"]
    web_thickness: float = DEFAULTS["web_thickness"]
    journal_width: float = DEFAULTS["journal_width"]
    deck_clearance: float = DEFAULTS["deck_clearance"]
    piston_clearance: float = DEFAULTS["piston_clearance"]
    skirt_length: float = DEFAULTS["skirt_length"]          # below the piston pin centre
    rod_swing_clearance: float = DEFAULTS["rod_swing_clearance"]   # crankcase bay radius beyond the big-end orbit
    crank_angle: float = DEFAULTS["crank_angle"]

    def problems(self) -> list[str]:
        p, out = self, []
        for k in ("bore", "stroke", "rod_length", "compression_height", "main_journal_d", "crankpin_d",
                  "piston_pin_d", "rod_width", "web_thickness", "journal_width"):
            if not getattr(p, k) > 0:
                out.append(f"{k} must be > 0")
        if out:
            return out
        r = p.stroke / 2
        if sorted(p.firing_order) != [1, 2, 3, 4, 5, 6]:
            out.append("firing_order must name cylinders 1..6 once each")
        if not 30 <= p.bank_angle <= 120:
            out.append("bank_angle must be 30..120 degrees")
        if p.rod_length < 2.5 * r:
            out.append(f"rod_length {p.rod_length:g} is under 2.5 x crank radius ({2.5 * r:g}): the rod angle is too steep")
        if p.crankpin_d / 2 <= r * math.sin(math.radians(30)) and p.bank_angle == 60:
            out.append("split crankpins 60 degrees apart would not overlap: crankpin_d must exceed the crank radius")
        if p.piston_pin_d >= p.compression_height:
            out.append("piston_pin_d must be smaller than the compression height")
        if not 0 < p.piston_clearance < 1:
            out.append("piston_clearance must be between 0 and 1 mm")
        if not p.piston_pin_d / 2 + 6.0 < p.skirt_length < p.bore:
            out.append("skirt_length must clear the pin boss and stay under one bore")
        return out


def _dir(angle_deg: float):
    """A direction in the YZ plane at angle a from +Z toward +Y."""
    a = math.radians(angle_deg)
    return (0.0, math.sin(a), math.cos(a))


def derive(p: V6Params) -> dict:
    """Every derived number with its relation - what the template builds and what the checks recompute."""
    r = p.stroke / 2
    half = p.bank_angle / 2
    bank = {c: ("R" if c % 2 else "L") for c in range(1, 7)}
    beta = {"R": half, "L": -half}
    place = {c: p.firing_order.index(c) for c in range(1, 7)}
    phase = {c: (beta[bank[c]] + 120.0 * place[c]) % 360.0 for c in range(1, 7)}
    pin_w = p.rod_width + 1.0
    pitch = p.journal_width + 2 * p.web_thickness + 2 * pin_w
    x_pin, x = {}, 0.0
    layout = []                                  # (kind, x0, x1, cylinder or None)
    for k in range(3):
        layout.append(("journal", x, x + p.journal_width, None)); x += p.journal_width
        a, b = 2 * k + 1, 2 * k + 2                # one cylinder of each bank per throw pair
        layout.append(("web", x, x + p.web_thickness, a)); x += p.web_thickness
        for c in (a, b):
            layout.append(("pin", x, x + pin_w, c)); x_pin[c] = x + pin_w / 2; x += pin_w
        layout.append(("web", x, x + p.web_thickness, b)); x += p.web_thickness
    layout.append(("journal", x, x + p.journal_width, None)); x += p.journal_width
    deck = r + p.rod_length + p.compression_height + p.deck_clearance
    tdc = {c: (phase[c] - beta[bank[c]]) % 360.0 for c in range(1, 7)}
    return {"r": r, "bank": bank, "beta": beta, "phase": phase, "tdc_crank_angle": tdc, "pin_width": pin_w,
            "pitch": pitch, "x_pin": x_pin, "layout": layout, "length": x, "deck_height": deck,
            "relations": {"r": "stroke / 2", "deck_height": "r + rod_length + compression_height + deck_clearance",
                          "phase": "bank axis angle + 120 x place in firing order (mod 360)",
                          "pitch": "journal_width + 2 web_thickness + 2 (rod_width + 1)"}}


def slider_crank(p: V6Params, d: dict, c: int, crank_angle: float):
    """(crankpin centre, piston pin centre, bank axis) for cylinder c at a crank angle (degrees)."""
    r, l = d["r"], p.rod_length
    beta = d["beta"][d["bank"][c]]
    psi = d["phase"][c] - crank_angle                         # the crank turns +X: a phase psi moves to psi - angle
    pin = (d["x_pin"][c], *_dir(psi)[1:])
    pin = (pin[0], r * pin[1], r * pin[2])
    t = math.radians(psi - beta)
    s = r * math.cos(t) + math.sqrt(l * l - (r * math.sin(t)) ** 2)
    b = _dir(beta)
    wrist = (d["x_pin"][c], s * b[1], s * b[2])
    return pin, wrist, b


# ---- parts (each in its own coordinates; interfaces say where it connects) ----------------------------------------

def crankshaft(p: V6Params, d: dict) -> dict:
    r, rj, rp = d["r"], p.main_journal_d / 2, p.crankpin_d / 2
    feats = [S.plane_x("p_nose", -40.0), S.loops("s_nose", "p_nose", [S.circle(0, 0, rj * 0.6)]), S.extrude("e_nose", 40)]
    pins = {}
    for n, (kind, x0, x1, c) in enumerate(d["layout"]):
        pl = f"p{n}"
        feats.append(S.plane_x(pl, x0))
        if kind == "journal":
            lp = [S.circle(0, 0, rj)]
        elif kind == "pin":
            ph = math.radians(d["phase"][c])
            pins[c] = (r * math.sin(ph), r * math.cos(ph))
            lp = [S.circle(*pins[c], rp)]
        else:
            ph = math.radians(d["phase"][c])
            lp = [S.slot((0.0, 0.0), rj + 10.0, (r * math.sin(ph), r * math.cos(ph)), rp + 7.0)]
        feats += [S.loops(f"s{n}", pl, lp), S.extrude(f"e{n}", x1 - x0)]
    feats += [S.plane_x("p_flange", d["length"]), S.loops("s_flange", "p_flange", [S.circle(0, 0, rj + 24)]),
              S.extrude("e_flange", 14)]
    ifaces = {"main_axis": S.frame([0, 0, 0], [0, 1, 0], [1, 0, 0])}
    for c, (u, v) in pins.items():
        ifaces[f"pin_{c}"] = S.frame([d["x_pin"][c], u, v], [0, 1, 0], [1, 0, 0])
    return S.part("v6_crankshaft", feats, ifaces)


def connecting_rod(p: V6Params, d: dict) -> dict:
    """Length along +Z, pin axes along +X, centred on x = 0; big end at the origin."""
    l, w = p.rod_length, p.rod_width
    rb, rs = p.crankpin_d / 2, p.piston_pin_d / 2
    rbe, rse = rb + 8.0, rs + 6.0
    face = S.plane("p_face", [-w / 2, 0, 0], [1, 0, 0], [0, 0, 1])          # u = +Z, v = -Y
    outline = S.slot((0.0, 0.0), rbe, (l, 0.0), rse)
    feats = [face, S.loops("s_rod", "p_face", [outline, S.circle(0, 0, rb), S.circle(l, 0, rs)]), S.extrude("e_rod", w)]
    # I-beam: a pocket in each face, 4 mm inside the outline, leaving a 6 mm web
    sg = (rbe - rse) / l
    cg = math.sqrt(1 - sg * sg)

    def half_width(xi):
        return rbe * cg - (xi - rbe * sg) * sg / cg
    u1, u2 = rbe + 4.0, l - rse - 4.0
    h1, h2 = half_width(u1) - 4.0, half_width(u2) - 4.0
    depth = (w - 6.0) / 2
    feats += [S.plane("p_top", [w / 2, 0, 0], [1, 0, 0], [0, 0, 1]),
              S.loops("s_pocket", "p_top", [S.polygon([(u1, -h1), (u2, -h2), (u2, h2), (u1, h1)])]),
              S.cut("k_pocket", depth, direction="reverse"),
              S.plane("p_mid", [0, 0, 0], [1, 0, 0], [0, 0, 1]),
              {"$op": "feature.mirror", "id": "m_pocket", "args": {"plane": "p_mid", "features": ["k_pocket"]}}]
    ifaces = {"big_end": S.frame([0, 0, 0], [0, 1, 0], [1, 0, 0]), "small_end": S.frame([0, 0, l], [0, 1, 0], [1, 0, 0])}
    return S.part("v6_connecting_rod", feats, ifaces)


def piston(p: V6Params, d: dict) -> dict:
    """Axis along +Z (crown up), piston pin along +X through the origin."""
    R = (p.bore - p.piston_clearance) / 2
    hc, ts, tc = p.compression_height, 4.0, 8.0
    rin, ls = R - ts, p.skirt_length
    sec = S.plane("p_sec", [0, 0, 0], [0, -1, 0], [0, 0, 1])                # u = +Z (the axis), v = -X
    prof = S.polygon([(-ls, rin), (-ls, R), (hc, R), (hc, 0.0), (hc - tc, 0.0), (hc - tc, rin)])
    feats = [sec, S.loops("s_body", "p_sec", [prof]), {"$op": "feature.revolve", "id": "r_body", "args": {"axis": "u"}}]
    for k in range(3):                                                         # three ring grooves under the crown
        u0 = hc - 4.0 - k * 4.5 - 2.0
        feats += [S.loops(f"s_g{k}", "p_sec", [S.polygon([(u0, R - 3.0), (u0 + 2.0, R - 3.0), (u0 + 2.0, R + 1.0), (u0, R + 1.0)])]),
                  {"$op": "feature.cut_revolve", "id": f"g{k}", "args": {"axis": "u"}}]
    feats += [S.plane("p_pin", [-R - 5.0, 0, 0], [1, 0, 0], [0, 1, 0]),
              {"$op": "feature.hole", "id": "h_pin", "sketched_on": "p_pin",
               "args": {"diameter": p.piston_pin_d, "through_all": True, "centers": [[0, 0]]}}]
    # slipper skirt: below the pin bosses the skirt is kept only on the thrust sides (+-Y); the pin sides (+-X) are cut
    # away - what lets the pistons of a cylinder pair on opposite banks pass each other near the bottom of the stroke
    xc, zc = 0.3 * p.bore, -(p.piston_pin_d / 2 + 4.0)
    side = S.plane("p_side", [0, -R - 5.0, 0], [0, 1, 0], [1, 0, 0])          # u = +X, v = -Z
    win = [S.polygon([(xc, -zc), (R + 1.0, -zc), (R + 1.0, ls + 1.0), (xc, ls + 1.0)]),
           S.polygon([(-R - 1.0, -zc), (-xc, -zc), (-xc, ls + 1.0), (-R - 1.0, ls + 1.0)])]
    feats += [side, S.loops("s_slipper", "p_side", win), S.cut("k_slipper", through_all=True)]
    ifaces = {"axis": S.frame([0, 0, 0], [1, 0, 0], [0, 0, 1]), "pin_axis": S.frame([0, 0, 0], [0, 1, 0], [1, 0, 0])}
    return S.part("v6_piston", feats, ifaces)


def piston_pin(p: V6Params, d: dict) -> dict:
    R = (p.bore - p.piston_clearance) / 2
    length = 2 * (R - 2.0)
    feats = [S.plane_x("p_end", -length / 2),
             S.loops("s_pin", "p_end", [S.circle(0, 0, p.piston_pin_d / 2), S.circle(0, 0, 0.3 * p.piston_pin_d)]),
             S.extrude("e_pin", length)]
    return S.part("v6_piston_pin", feats, {"axis": S.frame([0, 0, 0], [0, 1, 0], [1, 0, 0])})


def block(p: V6Params, d: dict) -> dict:
    """In engine coordinates (placed at the origin)."""
    rj, deck, length = p.main_journal_d / 2, d["deck_height"], d["length"]
    wall, cav = 8.0, d["r"] + p.crankpin_d / 2 + 8.0 + p.rod_swing_clearance   # bay radius: big-end orbit + clearance
    feats = [S.plane_x("p0", 0.0)]
    # crankcase: a box under and around the crank
    feats += [S.loops("s_case", "p0", [S.polygon([(-cav - wall, -cav - wall), (cav + wall, -cav - wall),
                                                    (cav + wall, cav * 0.5), (-cav - wall, cav * 0.5)])]),
              S.extrude("e_case", length)]
    for side, beta in (("R", d["beta"]["R"]), ("L", d["beta"]["L"])):
        b, half = _dir(beta), p.bore / 2 + wall
        n = (b[2], -b[1])                                             # in-plane perpendicular to the bank axis (u=Y, v=Z)
        pts = [(s * b[1] + k * half * n[0], s * b[2] + k * half * n[1]) for s, k in ((0.0, -1), (0.0, 1), (deck, 1), (deck, -1))]
        feats += [S.loops(f"s_bank{side}", "p0", [S.polygon(pts)]), S.extrude(f"e_bank{side}", length)]
    feats += [S.loops("s_main", "p0", [S.circle(0, 0, rj)]), S.cut("k_main", through_all=True)]
    ifaces = {"main_bore": S.frame([0, 0, 0], [0, 1, 0], [1, 0, 0])}
    js = [(x0, x1) for kind, x0, x1, _ in d["layout"] if kind == "journal"]
    for k in range(3):                                                 # bays between the bulkheads
        x0, x1 = js[k][1], js[k + 1][0]
        feats += [S.plane_x(f"p_bay{k}", x0), S.loops(f"s_bay{k}", f"p_bay{k}", [S.circle(0, 0, cav)]),
                  S.cut(f"k_bay{k}", x1 - x0, direction="normal")]
    for c in range(1, 7):
        b = _dir(d["beta"][d["bank"][c]])
        top = (d["x_pin"][c], deck * b[1], deck * b[2])
        feats += [S.plane(f"p_deck{c}", top, b, [1, 0, 0]),
                  {"$op": "feature.hole", "id": f"bore{c}", "sketched_on": f"p_deck{c}",
                   "args": {"diameter": p.bore, "depth": deck - 0.5 * cav, "direction": "reverse", "centers": [[0, 0]]}}]
        ifaces[f"bore_{c}"] = S.frame(top, [1, 0, 0], b)
    return S.part("v6_block", feats, ifaces)


# ---- the assembly at a crank angle -----------------------------------------------------------------------------------

def assembly(p: V6Params | None = None) -> dict:
    p = p or V6Params()
    bad = p.problems()
    if bad:
        raise ValueError("V6 parameters: " + "; ".join(bad))
    d = derive(p)
    a = p.crank_angle
    parts = {"crankshaft": {"spec": crankshaft(p, d)}, "rod": {"spec": connecting_rod(p, d)},
             "piston": {"spec": piston(p, d)}, "pin": {"spec": piston_pin(p, d)}, "block": {"spec": block(p, d)}}
    rot = math.radians(a)
    comps = [{"id": "block", "part": "block", "frame": S.frame([0, 0, 0], [1, 0, 0], [0, 0, 1])},
             {"id": "crankshaft", "part": "crankshaft",
              "frame": S.frame([0, 0, 0], [1, 0, 0], [0, -math.sin(rot), math.cos(rot)])}]
    mates = [{"type": "concentric", "a": ["crankshaft", "main_axis"], "b": ["block", "main_bore"]}]
    contacts = [["crankshaft", "block"]]
    for c in range(1, 7):
        pin, wrist, b = slider_crank(p, d, c, a)
        rod_z = tuple(w - q for w, q in zip(wrist, pin))
        comps += [{"id": f"rod_{c}", "part": "rod", "frame": S.frame(pin, [1, 0, 0], rod_z)},
                  {"id": f"piston_{c}", "part": "piston", "frame": S.frame(wrist, [1, 0, 0], b)},
                  {"id": f"pin_{c}", "part": "pin", "frame": S.frame(wrist, [1, 0, 0], b)}]
        mates += [{"type": "concentric", "a": [f"rod_{c}", "big_end"], "b": ["crankshaft", f"pin_{c}"]},
                  {"type": "concentric", "a": [f"rod_{c}", "small_end"], "b": [f"pin_{c}", "axis"]},
                  {"type": "concentric", "a": [f"pin_{c}", "axis"], "b": [f"piston_{c}", "pin_axis"]},
                  {"type": "coaxial", "a": [f"piston_{c}", "axis"], "b": ["block", f"bore_{c}"]}]
        contacts += [[f"rod_{c}", "crankshaft"], [f"rod_{c}", f"pin_{c}"], [f"pin_{c}", f"piston_{c}"]]
    return {"assembly": "v6_engine_core", "units": "mm", "language": 2, "parts": parts, "components": comps,
            "mates": mates, "contacts": contacts, "params": asdict(p),
            "derived": {k: v for k, v in d.items() if k not in ("layout",)}}


def kinematic_checks(asm_result, p: V6Params) -> list[dict]:
    """Recomputed from the placed interfaces (not from the template's own numbers): rod centre distances, piston pins on
    their bank axes, and even 120-degree firing from the crank's pin phases."""
    d = derive(p)
    out = []
    comps = asm_result.components
    for c in range(1, 7):
        be = comps[f"rod_{c}"].interfaces["big_end"]["origin"]
        se = comps[f"rod_{c}"].interfaces["small_end"]["origin"]
        cp = comps["crankshaft"].interfaces[f"pin_{c}"]["origin"]
        pp = comps[f"pin_{c}"].interfaces["axis"]["origin"]
        rod = math.dist(cp, pp)
        out.append({"name": f"kinematics:rod_{c}_centres", "outcome": "passed" if abs(rod - p.rod_length) <= 1e-6 else "failed",
                    "expected": f"{p.rod_length:g} mm", "actual": f"{rod:.9g} mm", "why": None})
        bore = comps["block"].interfaces[f"bore_{c}"]
        off = math.dist((0, 0, 0), tuple(a - b for a, b in zip(
            tuple(q - o for q, o in zip(pp, bore["origin"])),
            tuple(bore["z"][i] * sum((pp[j] - bore["origin"][j]) * bore["z"][j] for j in range(3)) for i in range(3)))))
        out.append({"name": f"kinematics:piston_pin_{c}_on_bore_axis", "outcome": "passed" if off <= 1e-6 else "failed",
                    "expected": "0 mm off the bore axis", "actual": f"{off:.3g} mm", "why": None})
        out.append({"name": f"kinematics:rod_{c}_big_end_on_pin", "outcome": "passed" if math.dist(be, cp) <= 1e-6 else "failed",
                    "expected": "0 mm", "actual": f"{math.dist(be, cp):.3g} mm", "why": None})
        _ = se
    # firing: TDC angle of each cylinder from the crank's own pin interfaces (part coordinates = crank angle 0)
    spec = asm_result.parts["crankshaft"]
    del spec
    tdc = {}
    crank_if = comps["crankshaft"]
    rot = math.radians(p.crank_angle)
    for c in range(1, 7):
        o = crank_if.interfaces[f"pin_{c}"]["origin"]
        psi_now = math.degrees(math.atan2(o[1], o[2]))            # angle from +Z toward +Y, as placed
        psi0 = psi_now + p.crank_angle                             # back to crank angle 0
        tdc[c] = (psi0 - d["beta"][d["bank"][c]]) % 360.0
    order = p.firing_order
    steps = [round(((tdc[order[(i + 1) % 6]] - tdc[order[i]]) % 360.0), 6) for i in range(6)]
    ok = all(abs(s - 120.0) <= 1e-6 for s in steps)
    out.append({"name": "kinematics:even_firing", "outcome": "passed" if ok else "failed",
                "expected": "120 deg between consecutive cylinders in the firing order (mod 360)",
                "actual": f"{steps}", "why": None})
    del rot
    return out


def sweep(p: V6Params, step_deg: float = 10.0, build_part=None) -> dict:
    """Interference and mates at every crank angle in steps through one revolution (parts built once, then placed).
    {angle: [failed checks]} - an empty list at every angle means the core turns over without a clash."""
    import json
    from dataclasses import replace

    from astracad_geom.assembly import build_assembly
    from astracad_geom.occ_build import build
    cache: dict[str, object] = {}

    def cached(spec):
        key = json.dumps(spec, sort_keys=True)
        if key not in cache:
            cache[key] = (build_part or build)(spec)
        return cache[key]
    out = {}
    n = int(round(360.0 / step_deg))
    for k in range(n):
        q = replace(p, crank_angle=k * step_deg)
        res = build_assembly(assembly(q), part_builder=cached)
        out[k * step_deg] = [c for c in res.checks + kinematic_checks(res, q) if c["outcome"] != "passed"]
    return out
