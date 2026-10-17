"""General tolerances: limits for dimensions that state none of their own.

Most dimensions on a production drawing carry no tolerance of their own; a general tolerance —
an ISO 2768-1 class, or a title-block rule such as "TWO PLACE DECIMAL ±0.01" — governs them.
Without it an inspector has nothing to pass or fail against. This module:

* reads a general-tolerance statement from the drawing's own text, or takes one the user declares
  (``--general-tolerance iso2768-m`` / ``places:2=0.01,3=0.005``); nothing is assumed when neither
  exists;
* applies it only to linear, diameter and radius characteristics that have a nominal and state no
  tolerance, as a distinct tolerance type (``GeneralTolerance``) so a derived limit is never
  confused with one printed on the dimension;
* refuses where the rule does not reach: values outside the table's size range, ISO 2768 on an
  inch drawing, decimal-place rules for a place count the drawing does not state, and angles
  (ISO 2768 angular tolerance depends on the shorter leg's length, which a dimension does not carry).

ISO 2768-1:1989 values below are transcribed from Tables 1 and 2 as widely reproduced in
engineering references; verify them against a licensed copy of the standard before relying on them
for acceptance. The source is recorded on every row that uses them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import CharKind, GeneralTolerance, NoTolerance

ISO2768_SOURCE = ("ISO 2768-1:1989 Table 1 (linear) / Table 2 (radius, chamfer), as transcribed "
                  "in ASTraCAD general_tol.py; verify against a licensed copy")

# (upper bound of the nominal range in mm, inclusive), tolerance ± for classes f, m, c, v
_LINEAR = [
    (3.0, {"f": 0.05, "m": 0.1, "c": 0.2, "v": None}),        # over 0.5 up to 3
    (6.0, {"f": 0.05, "m": 0.1, "c": 0.3, "v": 0.5}),
    (30.0, {"f": 0.1, "m": 0.2, "c": 0.5, "v": 1.0}),
    (120.0, {"f": 0.15, "m": 0.3, "c": 0.8, "v": 1.5}),
    (400.0, {"f": 0.2, "m": 0.5, "c": 1.2, "v": 2.5}),
    (1000.0, {"f": 0.3, "m": 0.8, "c": 2.0, "v": 4.0}),
    (2000.0, {"f": 0.5, "m": 1.2, "c": 3.0, "v": 6.0}),
    (4000.0, {"f": None, "m": 2.0, "c": 4.0, "v": 8.0}),
]
_RADIUS = [  # external radii and chamfer heights
    (3.0, {"f": 0.2, "m": 0.2, "c": 0.4, "v": 0.4}),
    (6.0, {"f": 0.5, "m": 0.5, "c": 1.0, "v": 1.0}),
    (float("inf"), {"f": 1.0, "m": 1.0, "c": 2.0, "v": 2.0}),
]
_PLACES = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4}


@dataclass
class GeneralSpec:
    iso_class: str | None = None                  # f, m, c, v
    places: dict[int, float] = field(default_factory=dict)
    source: str = ""                              # where the statement came from
    evidence: str = ""                            # the text it was read from

    @property
    def empty(self) -> bool:
        return self.iso_class is None and not self.places


def parse_user(spec: str) -> GeneralSpec:
    """'iso2768-m' or 'places:2=0.01,3=0.005' (as declared by the user on the command line)."""
    t = spec.strip().lower()
    m = re.fullmatch(r"iso\s*2768\s*-?\s*([fmcv])[hkl]?", t)
    if m:
        return GeneralSpec(iso_class=m.group(1), source="user-specified", evidence=spec)
    m = re.fullmatch(r"places\s*:\s*(.+)", t)
    if m:
        places = {}
        for part in m.group(1).split(","):
            k, _, v = part.partition("=")
            places[int(k)] = float(v)
        return GeneralSpec(places=places, source="user-specified", evidence=spec)
    raise ValueError(f"general tolerance {spec!r}: use iso2768-<f|m|c|v> or places:2=0.01,3=0.005")


def detect(texts: list[str]) -> GeneralSpec:
    """Find a general-tolerance statement in the drawing's own text. A template line with the
    values left blank ("TWO PLACE DECIMAL ±") states nothing and is ignored."""
    spec = GeneralSpec(source="drawing text")
    ev = []
    for raw in texts:
        t = " ".join(str(raw).replace("%%p", "±").replace("%%P", "±").split())
        m = re.search(r"ISO\s*2768\s*[-–]?\s*([fmcvFMCV])(?:[HKLhkl])?\b", t)
        if m and spec.iso_class is None:
            spec.iso_class = m.group(1).lower()
            ev.append(m.group(0))
        for m in re.finditer(r"\b(ONE|TWO|THREE|FOUR)\s+PLACE\s+DECIMAL\s*[±]\s*(\d*\.\d+|\d+)",
                             t, re.I):
            spec.places.setdefault(_PLACES[m.group(1).upper()], float(m.group(2)))
            ev.append(m.group(0))
        for m in re.finditer(r"\bX\.(X{1,4})\s*[±]\s*(\d*\.\d+)", t, re.I):
            spec.places.setdefault(len(m.group(1)), float(m.group(2)))
            ev.append(m.group(0))
    spec.evidence = "; ".join(ev)
    return spec


def _from_table(table, size: float, cls: str) -> float | None:
    if size < 0.5:
        return None
    for upper, row in table:
        if size <= upper:
            return row.get(cls)
    return None


def _decimals(callout: str) -> int | None:
    m = re.search(r"\d*\.(\d+)", callout or "")
    if m:
        return len(m.group(1))
    return 0 if re.search(r"\d", callout or "") else None


def apply(characteristics, spec: GeneralSpec, unit: str) -> dict[str, int]:
    """Attach general-tolerance limits to untoleranced rows. Returns counts by outcome."""
    from .limits import resolve_limits
    counts = {"applied": 0, "refused": 0}
    if spec.empty:
        return counts
    for c in characteristics:
        if not isinstance(c.tolerance, NoTolerance) or c.nominal is None:
            continue
        if c.kind not in (CharKind.LINEAR, CharKind.DIAMETER, CharKind.RADIUS):
            if c.kind is CharKind.ANGULAR:
                c.record("general_tolerance", "not applied",
                         "angular general tolerance depends on the shorter leg's length, which the "
                         "dimension does not state")
            continue
        pm, basis, why = None, "", ""
        if spec.iso_class:
            if unit != "mm":
                why = f"ISO 2768 is metric; this drawing is in {unit}"
            else:
                table = _RADIUS if c.kind is CharKind.RADIUS else _LINEAR
                pm = _from_table(table, abs(c.nominal), spec.iso_class)
                basis = f"ISO 2768-{spec.iso_class}"
                why = ("" if pm is not None else
                       f"{c.nominal:g} mm is outside the ISO 2768-{spec.iso_class} table")
        if pm is None and spec.places:
            n = _decimals(c.raw_callout)
            if n is not None and n in spec.places:
                pm, basis, why = spec.places[n], f"{n}-place decimal rule", ""
            elif not why:
                why = f"no general rule for {n}-place values (have {sorted(spec.places)})"
        if pm is None:
            counts["refused"] += 1
            c.record("general_tolerance", "not applied", why)
            continue
        c.tolerance = GeneralTolerance(pm, basis)
        c.limits = resolve_limits(c.nominal, c.tolerance, unit)
        c.issues = [i for i in c.issues if i.code != "no_limits"]
        c.record("general_tolerance", "applied",
                 f"±{pm:g} from {basis} ({spec.source}: {spec.evidence or 'declared'}; "
                 f"{ISO2768_SOURCE if basis.startswith('ISO') else 'rule as stated'})")
        counts["applied"] += 1
    return counts
