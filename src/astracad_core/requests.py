"""Deterministic engineering-request interpreter (constitution Part 6, steps 1–7).

Turns a short natural-language request into a structured specification, or into a list of the
information that is missing. It does not guess: a dimension without a unit, a shape it does not
recognise, or a target application it cannot identify is reported as missing, never defaulted.

Supported request shapes (anything else is UNRECOGNISED, with the reason):
- block / plate / prism  W x H x D <unit>  [with a <d> <unit> (through) hole]
- cylinder / shaft / rod / disc  diameter|dia|Ø <d> <unit>  length|long|height|thick <l> <unit>
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

    # ---- create a block ---------------------------------------------------------------------
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
        hole = re.search(r"\b(\d+(?:\.\d+)?)" + _UNIT + r"\s*(?:diameter\s*)?(?:through\s*|thru\s*)?"
                         r"(?:hole|bore)", low) or re.search(
            r"\b(?:hole|bore)\b[^0-9]*?(?:diameter\s*)?" + _NUM + _UNIT, low)
        if hole:
            dh = _to_mm(hole.group(1), _norm_unit(hole.group(2)), "hole_diameter", it,
                        hole.group(0))
            if dh is None:
                return it
            if dh >= min(w, h):
                it.ambiguities.append(f"hole diameter {dh} mm does not fit a {w} x {h} mm face")
                return it
            feats.append({"$op": "feature.hole", "id": "bore", "sketched_on": "front",
                          "args": {"diameter": dh, "through_all": True, "center": [w / 2, h / 2]}})
            it.facts.append(Fact("hole_position", "face centre", None, "USER_SPECIFIED"
                                 if "centre" in low or "center" in low else "AI_PROPOSED",
                                 "not stated; centred on the sketch face" if "cent" not in low
                                 else hole.group(0)))
            if "cent" not in low:
                it.ambiguities.append("hole position not stated (proposal: centre of the "
                                      "W x H face) — confirm before execution")
            vol -= math.pi * (dh / 2) ** 2 * d
        it.expect = {"size_mm": [w, h, d], "volume_mm3": vol}
        it.facts.append(Fact("volume_mm3", vol, "mm^3", "CALCULATED",
                             "w*h*d" + (" - pi*(dh/2)^2*d" if hole else "")))
        tag = f"block_{w:g}x{h:g}x{d:g}" + (f"_hole{dh:g}" if hole else "")
        it.spec = {"name": _safe_name(tag), "units": "mm",
                   "features": feats, "exports": [{"format": e} for e in exports],
                   "notes": {"request": text}}
        return it

    # ---- create a cylinder ------------------------------------------------------------------
    m = re.search(r"\b(cylinder|shaft|rod|disc|disk|pin)\b", low)
    if m:
        it = Interpretation(text, "create_part", app, exports=exports)
        if app is None:
            it.missing.append("target CAD application")
        dm = re.search(r"\b(?:diameter|dia)\b\s*(?:of\s*)?" + _NUM + _UNIT, low)
        rm = re.search(r"\bradius\b\s*(?:of\s*)?" + _NUM + _UNIT, low)
        lm = re.search(r"\b(?:length|long|height|thick(?:ness)?)\b\s*(?:of\s*)?" + _NUM + _UNIT,
                       low) or re.search(_NUM + _UNIT + r"\s*(?:long|length|thick)\b", low)
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
        vol = math.pi * (dia / 2) ** 2 * length
        it.expect = {"size_mm": [dia, dia, length], "volume_mm3": vol}
        it.facts.append(Fact("volume_mm3", vol, "mm^3", "CALCULATED", "pi*(d/2)^2*L"))
        it.spec = {"name": _safe_name(f"{m.group(1)}_d{dia:g}_l{length:g}"), "units": "mm",
                   "features": [{"$op": "sketch.circle", "id": "disc", "sketched_on": "front",
                                 "args": {"radius": dia / 2, "x": 0, "y": 0}},
                                {"$op": "feature.extrude", "id": "body",
                                 "args": {"depth": length}}],
                   "exports": [{"format": e} for e in exports], "notes": {"request": text}}
        return it

    it = Interpretation(text, "unrecognised", app, exports=exports)
    it.missing.append("a supported shape (block/plate/prism or cylinder/shaft/rod) or a "
                      "'change <dimension> of <document> to <value> <unit>' instruction")
    return it
