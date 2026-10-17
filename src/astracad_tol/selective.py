"""Selective assembly: grouping hole and shaft tolerance bands to tighten the fit.

Both bands are split into ``n`` equal-width groups; group i holes mate only with group i shafts.
With equal hole and shaft band widths, every group then has the same clearance range and the
overall variation shrinks by n. With unequal widths the group clearances drift, and the result
states that rather than presenting a uniform improvement.

``match_measured`` works on *measured* sizes (from inspection, supplied by the caller). Parts
outside their limits are rejected with the reason; parts left without a partner in their group
are reported as surplus. Nothing is generated: this module never fabricates measurements.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .fits import evaluate


@dataclass
class Group:
    index: int
    hole: tuple[float, float]
    shaft: tuple[float, float]
    clearance_min: float
    clearance_max: float


@dataclass
class SelectivePlan:
    groups: list[Group]
    interchangeable: tuple[float, float]
    selective: tuple[float, float]
    equal_widths: bool
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _split(lo: float, hi: float, n: int) -> list[tuple[float, float]]:
    w = (hi - lo) / n
    return [(round(lo + i * w, 9), round(lo + (i + 1) * w, 9)) for i in range(n)]


def plan(hole: tuple[float, float], shaft: tuple[float, float], n: int) -> SelectivePlan:
    if not 2 <= n <= 50:
        raise ValueError("number of groups must be within 2..50")
    full = evaluate(hole, shaft)
    hs, ss = _split(*hole, n), _split(*shaft, n)
    groups = []
    for i, (h, s) in enumerate(zip(hs, ss), start=1):
        f = evaluate(h, s)
        groups.append(Group(i, h, s, f.clearance_min, f.clearance_max))
    wh, ws = hole[1] - hole[0], shaft[1] - shaft[0]
    eq = abs(wh - ws) <= 1e-12
    sel = (min(g.clearance_min for g in groups), max(g.clearance_max for g in groups))
    notes = []
    if not eq:
        notes.append(f"hole band {wh:g} and shaft band {ws:g} differ: group clearances drift by "
                     f"{abs(wh - ws) / n:g} per group, so the selective range is wider than "
                     "one group's")
    return SelectivePlan(groups, (full.clearance_min, full.clearance_max), sel, eq, notes)


@dataclass
class MatchResult:
    pairs: list[dict]
    rejected: list[dict]
    surplus: list[dict]
    group_counts: list[dict]
    clearance_range: tuple[float, float] | None

    def to_dict(self) -> dict:
        return asdict(self)


def _group_of(x: float, bands: list[tuple[float, float]]) -> int | None:
    for i, (lo, hi) in enumerate(bands):
        last = i == len(bands) - 1
        if lo <= x < hi or (last and abs(x - hi) <= 1e-12) or (last and lo <= x <= hi):
            return i
    return None


def match_measured(holes: dict[str, float], shafts: dict[str, float],
                   hole: tuple[float, float], shaft: tuple[float, float], n: int) -> MatchResult:
    """holes/shafts map a part serial to its measured size."""
    hb, sb = _split(*hole, n), _split(*shaft, n)
    rejected, by_group = [], {i: ([], []) for i in range(n)}
    for kind, parts, bands, lim in (("hole", holes, hb, hole), ("shaft", shafts, sb, shaft)):
        for serial, x in sorted(parts.items()):
            g = _group_of(x, bands)
            if g is None:
                rejected.append({"part": kind, "serial": serial, "measured": x,
                                 "reason": f"outside limits {lim[0]:g}..{lim[1]:g}"})
                continue
            by_group[g][0 if kind == "hole" else 1].append((serial, x))
    pairs, surplus, counts = [], [], []
    for g, (hl, sl) in by_group.items():
        counts.append({"group": g + 1, "holes": len(hl), "shafts": len(sl)})
        hl.sort(key=lambda t: t[1])
        sl.sort(key=lambda t: t[1])
        for (hs, hx), (ss, sx) in zip(hl, sl):
            pairs.append({"group": g + 1, "hole": hs, "shaft": ss,
                          "clearance": round(hx - sx, 9)})
        for serial, x in hl[len(sl):]:
            surplus.append({"group": g + 1, "part": "hole", "serial": serial, "measured": x})
        for serial, x in sl[len(hl):]:
            surplus.append({"group": g + 1, "part": "shaft", "serial": serial, "measured": x})
    cr = (min(p["clearance"] for p in pairs), max(p["clearance"] for p in pairs)) if pairs else None
    return MatchResult(pairs, rejected, surplus, counts, cr)
