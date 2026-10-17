"""Deterministic engineering-request interpreter (constitution Part 6, steps 1–7).

Turns a short natural-language request into a structured specification, or into a list of the
information that is missing. It does not guess: a dimension without a unit, a shape it does not
recognise, or a target application it cannot identify is reported as missing, never defaulted.

Supported request shapes (anything else is UNRECOGNISED, with the reason):
- block / plate / prism  W x H x D <unit>  [holes, below]
- cylinder / shaft / rod / disc / flange  diameter|dia|Ø <d> <unit>  length|long|thick <l> <unit>
  [holes, below]
- stepped shaft  dia <D1> x <L1>, dia <D2> x <L2>, ...  (steps in order from one end; revolved)
- tube / pipe / sleeve / bush / spacer / washer / ring  OD <d>  ID <d> (or wall <t>)  length <l>
- holes: "a 10 mm hole at the centre"; "4 x 6 mm holes at 20 mm pitch along the width, centred"
  (one hole + linear pattern); "6 x 8 mm holes on a 70 mm PCD [starting at 30 deg]"
- change|set the <name> <dimension> of <document> to <v> <unit>   -> modify existing document
- each may name an application (SolidWorks, Creo, Fusion, AutoCAD) and "export STEP"
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

UNIT_MM = {"mm": 1.0, "millimetre": 1.0, "millimeter": 1.0, "millimetres": 1.0,
           "millimeters": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4,
           "inches": 25.4, '"': 25.4}
APPS = {"solidworks": "solidworks", "solid works": "solidworks", "sw": "solidworks",
        "creo": "creo", "proe": "creo", "pro/e": "creo", "fusion": "fusion",
        "fusion 360": "fusion", "autocad": "autocad", "acad": "autocad"}
_NUM = r"(\d+(?:\.\d+)?)"
_UNIT = r"\s*(mm|millimet(?:re|er)s?|cm|m|inch(?:es)?|in|\")?"


@dataclass
class Fact:
    """One interpreted value with where it came from — USER_SPECIFIED or CALCULATED."""
    name: str
    value: Any
    unit: str | None
    provenance: str
    source_text: str = ""


@dataclass
class Interpretation:
    request: str
    intent: str                     # create_part | modify_existing | unrecognised
    application: str | None
    facts: list[Fact] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)
    spec: dict[str, Any] | None = None
    expect: dict[str, Any] = field(default_factory=dict)
    exports: list[str] = field(default_factory=list)

    @property
    def executable(self) -> bool:
        return self.spec is not None and not self.missing and not self.ambiguities

    def fact(self, name: str) -> Any:
        return next((f.value for f in self.facts if f.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["executable"] = self.executable
        return d


def _to_mm(value: str, unit: str | None, label: str, it: Interpretation, src: str) -> float | None:
    if unit is None:
        it.missing.append(f"unit for {label} ({value} has no unit; nothing is assumed)")
        return None
    mm = float(value) * UNIT_MM[unit.lower() if unit != '"' else unit]
    it.facts.append(Fact(label, float(value), unit, "USER_SPECIFIED", src))
    if unit.lower() not in ("mm", "millimetre", "millimeter", "millimetres", "millimeters"):
        it.facts.append(Fact(f"{label}_mm", mm, "mm", "CALCULATED",
                             f"{value} {unit} x {UNIT_MM[unit.lower() if unit != chr(34) else unit]}"))
    return mm


def _norm_unit(u: str | None) -> str | None:
    if u is None:
        return None
    u = u.lower()
    if u.startswith("millimet"):
        return "mm"
    if u.startswith("inch"):
        return "in"
    return u


def _safe_name(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return (s or "part")[:40]


def interpret(request: str, default_app: str | None = None) -> Interpretation:
    text = request.strip()
    low = text.lower().replace("ø", "diameter ").replace("⌀", "diameter ").replace("×", "x")
    app = None
    hits = sorted({v for k, v in APPS.items() if re.search(rf"\b{re.escape(k)}\b", low)})
    if len(hits) > 1:
        it = Interpretation(text, "unrecognised", None)
        it.ambiguities.append(f"more than one CAD application named: {hits}")
        return it
    app = hits[0] if hits else default_app
    exports = ["step"] if re.search(r"\bstep\b|\bstp\b", low) else []

    # ---- modify an existing document, fully specified ---------------------------------------
    m = re.search(r"\b(?:change|set|modify|update)\s+(?P<dim>[A-Za-z]\w*@[\w\-]+(?:@[\w\-.]+)?)"
                  r"\s+(?:of|in|on)\s+(?P<path>\"[^\"]+\.sldprt\"|'[^']+\.sldprt'|\S+\.sldprt)"
                  r"\s+to\s+" + _NUM + _UNIT, text, re.I)
    if m:
        it = Interpretation(text, "modify_existing", app or "solidworks", exports=exports or ["step"])
        path = m.group("path").strip("\"'")
        it.facts.append(Fact("document", path, None, "USER_SPECIFIED", m.group("path")))
        it.facts.append(Fact("dimension_name", m.group("dim"), None, "USER_SPECIFIED", m.group("dim")))
        val = _to_mm(m.group(3), _norm_unit(m.group(4)), m.group("dim"), it, m.group(0))
        if app is None:
            it.facts.append(Fact("application", "solidworks", None, "CALCULATED",
                                 ".sldprt is a SOLIDWORKS part file"))
        if val is None:
            return it
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", path.replace("\\", "/").rsplit("/", 1)[-1][:-7])
        it.spec = {"source_path": path, "set": {m.group("dim"): val},
                   "name": (stem + "_rev")[:64]}
        it.expect = {"value_mm": val}
        return it

    # ---- modify an existing document --------------------------------------------------------
    m = re.search(r"\b(change|set|modify|update)\b.*?\b(diameter|length|width|height|depth|"
                  r"thickness|radius)\b.*?\bto\s+" + _NUM + _UNIT, low)
    doc = re.search(r"\"[^\"]+\.sldprt\"|'[^']+\.sldprt'|\S+\.sldprt\b", text, re.I)
    if m and (doc or re.search(r"\b(my|existing|open|the)\b.*\b(model|part|document|file)\b",
                               low)):
        inferred = doc is not None and app is None
        it = Interpretation(text, "modify_existing", "solidworks" if inferred else app,
                            exports=exports)
        if inferred:
            app = "solidworks"
            it.facts.append(Fact("application", "solidworks", None, "CALCULATED",
                                 ".sldprt is a SOLIDWORKS part file"))
        val = _to_mm(m.group(3), _norm_unit(m.group(4)), m.group(2), it, m.group(0))
        it.facts.append(Fact("target_dimension", m.group(2), None, "USER_SPECIFIED", m.group(0)))
        if doc:
            it.facts.append(Fact("document", doc.group(0).strip("\"'"), None, "USER_SPECIFIED",
                                 doc.group(0)))
        else:
            it.missing.append("which document (a file path or an open document title)")
        it.missing.append(f"which named model dimension is the {m.group(2)} "
                          "(e.g. D1@Sketch1) — not inferred from the word")
        if app is None:
            it.missing.append("target CAD application")
        if val is not None:
            it.expect["value_mm"] = val
        return it

    # ---- create a tube / washer / spacer (revolved annulus) --------------------------------------
    if re.search(r"\b(tube|pipe|sleeve|bush(?:ing)?|spacer|washer|ring)\b", low):
        return _tube(text, low, app, exports)

    # ---- create a stepped shaft (revolved profile) ------------------------------------------------
    segs = _segments(low)
    if re.search(r"\b(shaft|rod|pin|spindle|axle)\b", low) and (len(segs) >= 2 or "step" in low):
        return _stepped(text, low, app, exports, segs)

    # ---- create a block -----------------------------------------------------------------------
    m = re.search(r"\b(block|plate|prism|cuboid|box)\b", low)
    dims = re.search(_NUM + _UNIT + r"\s*x\s*" + _NUM + _UNIT + r"\s*x\s*" + _NUM + _UNIT, low)
    if m:
        it = Interpretation(text, "create_part", app, exports=exports)
        if app is None:
            it.missing.append("target CAD application")
        if not dims:
            it.missing.append("three dimensions as W x H x D with a unit")
            return it
        unit_last = _norm_unit(dims.group(6))
        units = [_norm_unit(dims.group(i)) or unit_last for i in (2, 4, 6)]
        w, h, d = (_to_mm(dims.group(i), u, n, it, dims.group(0))
                   for i, u, n in ((1, units[0], "width"), (3, units[1], "height"),
                                   (5, units[2], "depth")))
        if None in (w, h, d):
            return it
        feats = [{"$op": "sketch.rectangle", "id": "outline", "sketched_on": "front",
                  "args": {"width": w, "height": h}},
                 {"$op": "feature.extrude", "id": "body", "args": {"depth": d}}]
        vol = w * h * d
        rest = low[:dims.start()] + " " * len(dims.group(0)) + low[dims.end():]
        holes = _holes(it, rest, face="block", w=w, h=h, depth=d)
        if holes is None:
            return it
        feats += holes["features"]
        vol -= holes["removed_mm3"]
        it.expect = {"size_mm": [w, h, d], "volume_mm3": vol}
        it.facts.append(Fact("volume_mm3", vol, "mm^3", "CALCULATED",
                             "w*h*d" + (f" - {holes['count']}*pi*(dh/2)^2*d" if holes["count"] else "")))
        tag = f"block_{w:g}x{h:g}x{d:g}" + holes["tag"]
        it.spec = {"name": _safe_name(tag), "units": "mm",
                   "features": feats, "exports": [{"format": e} for e in exports],
                   "notes": {"request": text}}
        return it

    # ---- create a cylinder / disc -------------------------------------------------------------
    m = re.search(r"\b(cylinder|shaft|rod|disc|disk|pin|flange|spindle|axle)\b", low)
    if m:
        it = Interpretation(text, "create_part", app, exports=exports)
        if app is None:
            it.missing.append("target CAD application")
        dm = re.search(r"\b(?:diameter|dia|od)\b\s*(?:of\s*)?" + _NUM + _UNIT, low) or \
            re.search(_NUM + _UNIT + r"\s*(?:diameter|dia|od)\b", low)
        rm = re.search(r"\bradius\b\s*(?:of\s*)?" + _NUM + _UNIT, low)
        lm = re.search(r"\b(?:length|long|height|thick(?:ness)?)\b\s*(?:of\s*)?" + _NUM + _UNIT,
                       low) or re.search(_NUM + _UNIT + r"\s*(?:long|length|thick|high)\b", low)
        if dm and rm:
            it.ambiguities.append("both a diameter and a radius were given")
            return it
        if not (dm or rm):
            it.missing.append("diameter (or radius) with a unit")
        if not lm:
            it.missing.append("length with a unit")
        if it.missing:
            return it
        if dm:
            dia = _to_mm(dm.group(1), _norm_unit(dm.group(2)), "diameter", it, dm.group(0))
        else:
            r = _to_mm(rm.group(1), _norm_unit(rm.group(2)), "radius", it, rm.group(0))
            dia = None if r is None else 2 * r
        length = _to_mm(lm.group(1), _norm_unit(lm.group(2)), "length", it, lm.group(0))
        if dia is None or length is None:
            return it
        rest = low
        for g in (dm or rm, lm):
            rest = rest[:g.start()] + " " * (g.end() - g.start()) + rest[g.end():]
        holes = _holes(it, rest, face="disc", w=dia, h=dia, depth=length)
        if holes is None:
            return it
        vol = math.pi * (dia / 2) ** 2 * length - holes["removed_mm3"]
        it.expect = {"size_mm": [dia, dia, length], "volume_mm3": vol}
        it.facts.append(Fact("volume_mm3", vol, "mm^3", "CALCULATED", "pi*(d/2)^2*L" + (
            f" - {holes['count']}*pi*(dh/2)^2*L" if holes["count"] else "")))
        it.spec = {"name": _safe_name(f"{m.group(1)}_d{dia:g}_l{length:g}{holes['tag']}"),
                   "units": "mm",
                   "features": [{"$op": "sketch.circle", "id": "disc", "sketched_on": "front",
                                 "args": {"radius": dia / 2, "x": 0, "y": 0}},
                                {"$op": "feature.extrude", "id": "body",
                                 "args": {"depth": length}}] + holes["features"],
                   "exports": [{"format": e} for e in exports], "notes": {"request": text}}
        return it

    it = Interpretation(text, "unrecognised", app, exports=exports)
    it.missing.append("a supported shape (block/plate/prism, cylinder/shaft/disc, stepped shaft, "
                      "tube/washer/spacer) or a 'change <dimension> of <document> to <value> "
                      "<unit>' instruction")
    return it


# ---- helpers for the shape families ---------------------------------------------------------------
_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_COUNT = r"\b(\d+|" + "|".join(_WORDS) + r")"
_SEG_A = re.compile(r"\b(?:diameter|dia)\b\s*" + _NUM + _UNIT + r"\s*(?:x|by)\s*"
                    + _NUM + _UNIT + r"(?:\s*(?:long|length))?")
_SEG_B = re.compile(_NUM + _UNIT + r"\s*(?:diameter|dia)\b\s*(?:x|by)\s*"
                    + _NUM + _UNIT + r"(?:\s*(?:long|length))?")


def _count(s: str) -> int:
    return int(s) if s.isdigit() else _WORDS[s]


def _segments(low: str) -> list[re.Match]:
    """Every 'dia D x L' (or 'D dia x L') in the order written: the steps of a shaft."""
    out = list(_SEG_A.finditer(low))
    taken = [(m.start(), m.end()) for m in out]
    out += [m for m in _SEG_B.finditer(low)
            if not any(a <= m.start() < b or a < m.end() <= b for a, b in taken)]
    return sorted(out, key=lambda m: m.start())


def _shared_unit(it: Interpretation, units: list[str | None], label: str) -> list[str | None]:
    """A number written without a unit takes the request's unit only when exactly one unit was
    written for the whole list ("dia 20 x 50, dia 30 x 10 mm"); otherwise it stays missing."""
    given = {u for u in units if u}
    if len(given) == 1 and None in units:
        u = next(iter(given))
        it.facts.append(Fact(f"{label}_unit", u, None, "USER_SPECIFIED",
                             f"the only unit written in the {label} list applies to all of it"))
        return [u] * len(units)
    return units


def _stepped(text: str, low: str, app: str | None, exports: list[str],
             segs: list[re.Match]) -> Interpretation:
    it = Interpretation(text, "create_part", app, exports=exports)
    if app is None:
        it.missing.append("target CAD application")
    if len(segs) < 2:
        it.missing.append("each step as 'dia <D> x <L>' with a unit, in order from one end "
                          "(at least two steps)")
        return it
    units = _shared_unit(it, [_norm_unit(m.group(k)) for m in segs for k in (2, 4)], "step")
    steps = []
    for i, m in enumerate(segs):
        d = _to_mm(m.group(1), units[2 * i], f"step{i + 1}_diameter", it, m.group(0))
        ln = _to_mm(m.group(3), units[2 * i + 1], f"step{i + 1}_length", it, m.group(0))
        steps.append((d, ln))
    if any(None in s for s in steps):
        return it
    # Adjacent steps of one diameter are one cylinder: merging them keeps the profile free of
    # zero-length edges (which the runtime refuses) without changing the solid.
    merged: list[list[float]] = []
    for d, ln in steps:
        if merged and abs(merged[-1][0] - d) < 1e-9:
            merged[-1][1] += ln
        else:
            merged.append([d, ln])
    total = sum(ln for _, ln in merged)
    pts: list[list[float]] = [[0.0, 0.0], [total, 0.0]]
    x = total
    for d, ln in reversed(merged):
        pts.append([x, d / 2])
        x -= ln
        pts.append([x, d / 2])
    pts = [[round(a, 9), round(b, 9)] for a, b in pts]
    vol = sum(math.pi * (d / 2) ** 2 * ln for d, ln in merged)
    dmax = max(d for d, _ in merged)
    it.facts.append(Fact("steps", [[d, ln] for d, ln in merged], "mm", "USER_SPECIFIED",
                         "; ".join(m.group(0) for m in segs)))
    it.facts.append(Fact("volume_mm3", vol, "mm^3", "CALCULATED", "sum of pi*(d/2)^2*L per step"))
    it.facts.append(Fact("axis", "sketch X axis of the Front plane", None, "CALCULATED",
                         "a revolved profile needs one axis; it does not change the solid"))
    it.expect = {"size_mm": [dmax, dmax, total], "volume_mm3": vol}
    tag = "stepped_shaft_" + "_".join(f"d{d:g}l{ln:g}" for d, ln in merged)
    it.spec = {"name": _safe_name(tag), "units": "mm",
               "features": [{"$op": "sketch.profile", "id": "profile", "sketched_on": "front",
                             "args": {"points": pts}},
                            {"$op": "feature.revolve", "id": "body", "args": {}}],
               "exports": [{"format": e} for e in exports], "notes": {"request": text}}
    return it


def _tube(text: str, low: str, app: str | None, exports: list[str]) -> Interpretation:
    it = Interpretation(text, "create_part", app, exports=exports)
    if app is None:
        it.missing.append("target CAD application")
    kind = re.search(r"\b(tube|pipe|sleeve|bush(?:ing)?|spacer|washer|ring)\b", low).group(1)
    od = re.search(r"\b(?:od|outer\s+diameter|outside\s+diameter|outer\s+dia)\b\s*(?:of\s*)?"
                   + _NUM + _UNIT, low) or re.search(_NUM + _UNIT + r"\s*od\b", low)
    idm = re.search(r"\b(?:id|inner\s+diameter|inside\s+diameter|inner\s+dia|bore)\b\s*(?:of\s*)?"
                    + _NUM + _UNIT, low) or re.search(_NUM + _UNIT + r"\s*(?:id|bore)\b", low)
    lm = re.search(r"\b(?:length|long|height|thick(?:ness)?)\b\s*(?:of\s*)?" + _NUM + _UNIT,
                   low) or re.search(_NUM + _UNIT + r"\s*(?:long|length|thick|high)\b", low)
    wall = re.search(r"\bwall\b\s*(?:thickness\s*)?(?:of\s*)?" + _NUM + _UNIT, low) or \
        re.search(_NUM + _UNIT + r"\s*wall\b", low)
    if wall and lm and lm.start() >= wall.start() and lm.end() <= wall.end() + 12 and \
            "thick" in lm.group(0) and lm.group(1) == wall.group(1):
        lm = None                                        # "wall thickness 3" is not the length
        lm = re.search(r"\b(?:length|long|height)\b\s*(?:of\s*)?" + _NUM + _UNIT, low) or \
            re.search(_NUM + _UNIT + r"\s*(?:long|length|high)\b", low)
    if not od:
        it.missing.append("outer diameter (OD) with a unit")
    if not (idm or wall):
        it.missing.append("inner diameter (ID / bore) or wall thickness, with a unit")
    if idm and wall:
        it.ambiguities.append("both an inner diameter and a wall thickness were given")
    if lm is None:
        it.missing.append("length (or thickness) with a unit")
    if it.missing or it.ambiguities:
        return it
    second_m = idm or wall
    units = _shared_unit(it, [_norm_unit(g.group(2)) for g in (od, second_m, lm)], "size")
    do = _to_mm(od.group(1), units[0], "outer_diameter", it, od.group(0))
    second = _to_mm(second_m.group(1), units[1], "inner_diameter" if idm else "wall",
                    it, second_m.group(0))
    ln = _to_mm(lm.group(1), units[2], "length", it, lm.group(0))
    if None in (do, second, ln):
        return it
    di = second if idm else do - 2 * second
    if wall:
        it.facts.append(Fact("inner_diameter_mm", di, "mm", "CALCULATED", "OD - 2*wall"))
    if not 0 < di < do:
        it.ambiguities.append(f"inner diameter {di:g} mm must be between 0 and the outer "
                              f"diameter {do:g} mm")
        return it
    vol = math.pi / 4 * (do ** 2 - di ** 2) * ln
    it.facts.append(Fact("volume_mm3", vol, "mm^3", "CALCULATED", "pi/4*(OD^2-ID^2)*L"))
    it.expect = {"size_mm": [do, do, ln], "volume_mm3": vol}
    pts = [[0.0, di / 2], [ln, di / 2], [ln, do / 2], [0.0, do / 2]]
    it.spec = {"name": _safe_name(f"{kind}_od{do:g}_id{di:g}_l{ln:g}"), "units": "mm",
               "features": [{"$op": "sketch.profile", "id": "profile", "sketched_on": "front",
                             "args": {"points": pts}},
                            {"$op": "feature.revolve", "id": "body", "args": {}}],
               "exports": [{"format": e} for e in exports], "notes": {"request": text}}
    return it


_PCD = re.compile(r"(?:" + _NUM + _UNIT + r"\s*(?:diameter\s*)?(?:pcd|bcd|bolt\s+circle|"
                  r"pitch\s+circle)|(?:pcd|bcd|bolt\s+circle|pitch\s+circle)"
                  r"(?:\s+diameter)?\s*(?:of\s*)?" + _NUM + _UNIT + r")")


def _holes(it: Interpretation, low: str, face: str, w: float, h: float,
           depth: float) -> dict[str, Any] | None:
    """Through holes in the front face: one hole, a straight row, or a bolt circle.

    `face` is "block" (corner at the origin, W x H) or "disc" (centre at the origin, diameter W).
    Returns the hole features, or None when something is missing or ambiguous (recorded on `it`).
    Nothing about position is assumed: a location the request does not state is an ambiguity,
    except where the part's symmetry makes it immaterial (said so in the fact's source).
    """
    none = {"features": [], "removed_mm3": 0.0, "count": 0, "tag": ""}
    pat = re.search(_COUNT + r"\s*(?:x|off|no\.?)?\s*(?:diameter\s*)?" + _NUM + _UNIT
                    + r"\s*(?:diameter\s*|dia\s*)?(?:through\s*|thru\s*)?(?:holes|bores)\b", low) \
        or re.search(_COUNT + r"\s*(?:through\s*|thru\s*)?(?:holes|bores)\b\s*(?:of\s*)?"
                     r"(?:diameter\s*|dia\s*)?" + _NUM + _UNIT, low)
    single = None if pat else (
        re.search(r"\b(\d+(?:\.\d+)?)" + _UNIT + r"\s*(?:diameter\s*|dia\s*)?(?:through\s*|thru\s*)?"
                  r"(?:hole|bore)\b", low)
        or re.search(r"\b(?:hole|bore)\b[^0-9]*?(?:diameter\s*|dia\s*)?" + _NUM + _UNIT, low))
    if not (pat or single):
        if re.search(r"\b(holes?|bores?)\b", low):
            it.missing.append("hole diameter with a unit (and the number of holes)")
            return None
        return none
    centred = bool(re.search(r"\bcent(?:re|er|red|ered|ral)\b", low))
    if single:
        n, dh = 1, _to_mm(single.group(1), _norm_unit(single.group(2)), "hole_diameter", it,
                          single.group(0))
    else:
        n = _count(pat.group(1))
        dh = _to_mm(pat.group(2), _norm_unit(pat.group(3)), "hole_diameter", it, pat.group(0))
        it.facts.append(Fact("hole_count", n, None, "USER_SPECIFIED", pat.group(0)))
    if dh is None:
        return None
    if n < 1 or n > 48:
        it.ambiguities.append(f"{n} holes: supported counts are 1 to 48")
        return None
    cx, cy = (w / 2, h / 2) if face == "block" else (0.0, 0.0)
    centres: list[tuple[float, float]] = []
    extra: list[dict[str, Any]] = []
    if n == 1:
        it.facts.append(Fact("hole_position", "face centre" if face == "block" else "on the axis",
                             None, "USER_SPECIFIED" if centred else "AI_PROPOSED",
                             single.group(0) if centred else "not stated; proposed at the centre"))
        if not centred:
            it.ambiguities.append("hole position not stated (proposal: "
                                  + ("centre of the W x H face" if face == "block" else "on the axis")
                                  + ") - confirm before execution")
        centres = [(cx, cy)]
        tag = f"_hole{dh:g}"
    else:
        pcd = _PCD.search(low)
        pitch = re.search(r"\b(?:pitch|spacing|spaced|centres|centers|apart)\b\s*(?:of\s*|at\s*)?"
                          + _NUM + _UNIT, low) or re.search(
            _NUM + _UNIT + r"\s*(?:pitch|spacing|apart|centres|centers)\b", low)
        if pcd:
            v, u = (pcd.group(1), pcd.group(2)) if pcd.group(1) else (pcd.group(3), pcd.group(4))
            p = _to_mm(v, _norm_unit(u), "bolt_circle_diameter", it, pcd.group(0))
            if p is None:
                return None
            start = re.search(r"\bstart(?:ing)?\s*(?:at\s*)?(-?\d+(?:\.\d+)?)\s*(?:deg|degrees|°)",
                              low)
            a0 = float(start.group(1)) if start else 0.0
            if face == "block" and not centred:
                it.ambiguities.append("bolt circle centre not stated (proposal: centre of the "
                                      "W x H face) - say 'centred'")
            if start:
                it.facts.append(Fact("first_hole_angle_deg", a0, "deg", "USER_SPECIFIED",
                                     start.group(0)))
            elif face == "block":
                it.ambiguities.append("angle of the first hole not stated (proposal: 0 deg, on "
                                      "+X) - say 'starting at <A> deg'")
            else:
                it.facts.append(Fact("first_hole_angle_deg", 0.0, "deg", "AI_PROPOSED",
                                     "not stated; on a round part it turns the pattern only, "
                                     "the solid's size and volume do not change"))
            if p * math.sin(math.pi / n) <= dh:
                it.ambiguities.append(f"{n} x {dh:g} mm holes on a {p:g} mm circle overlap "
                                      f"(chord {p * math.sin(math.pi / n):.3g} mm)")
                return None
            centres = [(cx + p / 2 * math.cos(math.radians(a0 + 360 * k / n)),
                        cy + p / 2 * math.sin(math.radians(a0 + 360 * k / n))) for k in range(n)]
            tag = f"_{n}x{dh:g}_pcd{p:g}"
        elif pitch:
            s = _to_mm(pitch.group(1), _norm_unit(pitch.group(2)), "hole_pitch", it,
                       pitch.group(0))
            if s is None:
                return None
            if s <= dh:
                it.ambiguities.append(f"pitch {s:g} mm is not more than the hole diameter "
                                      f"{dh:g} mm: the holes would overlap")
                return None
            along = re.search(r"\balong\s+(?:the\s+)?(x|y|width|height|length|long\s+side)\b", low)
            axis = "X"
            if along:
                ax = along.group(1)
                axis = "X" if ax in ("x", "width") else "Y" if ax in ("y", "height") else (
                    "X" if w >= h else "Y")
                if ax not in ("x", "y", "width", "height") and w == h:
                    it.ambiguities.append("'along the length' of a square face names no direction")
                    return None
                it.facts.append(Fact("row_direction", axis, None, "USER_SPECIFIED", along.group(0)))
            else:
                it.ambiguities.append("row direction not stated - say 'along the width' (X) or "
                                      "'along the height' (Y)")
            if not centred:
                it.ambiguities.append("row position not stated (proposal: centred on the face) - "
                                      "say 'centred'")
            span = s * (n - 1)
            centres = [(cx - span / 2 + k * s, cy) if axis == "X" else (cx, cy - span / 2 + k * s)
                       for k in range(n)]
            extra = [{"$op": "feature.pattern.linear", "id": "row",
                      "args": {"feature": "hole1", "count": n, "spacing": s, "direction": axis}}]
            tag = f"_{n}x{dh:g}_p{s:g}"
        else:
            it.missing.append(f"where the {n} holes go: a pitch ('at 20 mm pitch') or a bolt "
                              "circle ('on a 50 mm PCD')")
            return None
    if it.ambiguities:
        return None
    for x, y in centres:                                  # every hole wholly inside the face
        inside = (dh / 2 < x < w - dh / 2 and dh / 2 < y < h - dh / 2) if face == "block" \
            else math.hypot(x, y) + dh / 2 < w / 2
        if not inside:
            it.ambiguities.append(f"a {dh:g} mm hole at ({x:.4g}, {y:.4g}) breaks out of the face")
            return None
    # Depth = the part's thickness, not "through all": on SW 2026 through-all resolved inside the
    # kernel left 0.4-2 ppm of material and a 6e-6 mm sliver in the STEP box (measured
    # 2026-10-17: disc 5 mm -> 5.000006); a dimensioned depth from the start face measured exact.
    if extra:
        feats = [{"$op": "feature.hole", "id": "hole1", "sketched_on": "front",
                  "args": {"diameter": dh, "depth": depth,
                           "center": [round(centres[0][0], 9), round(centres[0][1], 9)]}}] + extra
    else:
        feats = [{"$op": "feature.hole", "id": f"hole{k + 1}", "sketched_on": "front",
                  "args": {"diameter": dh, "depth": depth,
                           "center": [round(x, 9), round(y, 9)]}}
                 for k, (x, y) in enumerate(centres)]
    it.facts.append(Fact("hole_centres_mm", [[round(x, 6), round(y, 6)] for x, y in centres], "mm",
                         "CALCULATED", "from the stated count, diameter and pitch / circle"))
    return {"features": feats, "removed_mm3": n * math.pi * (dh / 2) ** 2 * depth, "count": n,
            "tag": tag}
