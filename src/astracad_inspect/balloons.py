"""Balloon numbering and layout.

Balloons are placed on a pre-separated lattice outside the drawn content, which makes
non-overlap a property of the layout rather than a hope: slot spacing exceeds the balloon
diameter by a declared gap, so two balloons cannot touch. A leader that would cross another
balloon moves that balloon to its next-nearest free slot, and a characteristic that finds no
free slot is reported as unplaced instead of being stacked on top of a neighbour.

Placement is deterministic: characteristics are laid out in stable cid order and ties break on
slot index, so the same drawing always produces the same layout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .model import Characteristic

MM = 1.0


@dataclass
class Balloon:
    cid: str
    number: int
    anchor: tuple[float, float]
    centre: tuple[float, float]
    radius: float
    leader: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class Layout:
    page: tuple[float, float, float, float]     # minx, miny, maxx, maxy (drawing units)
    balloons: list[Balloon] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)
    slot_pitch: float = 0.0
    ring_offset: float = 0.0


def _seg_circle_hit(a: tuple[float, float], b: tuple[float, float],
                    centre: tuple[float, float], radius: float) -> bool:
    """Does segment a-b pass within `radius` of `centre`? Used to keep leaders out of balloons."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 == 0:
        return math.hypot(ax - centre[0], ay - centre[1]) <= radius
    t = ((centre[0] - ax) * dx + (centre[1] - ay) * dy) / seg_len2
    t = max(0.0, min(1.0, t))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px - centre[0], py - centre[1]) <= radius


def _slots(page: tuple[float, float, float, float], content: tuple[float, float, float, float],
          pitch: float) -> list[tuple[float, float]]:
    """Two rings of candidate centres: one hugging the content, one further out, so a crowded
    first ring does not immediately force unplaced balloons."""
    minx, miny, maxx, maxy = page
    out: list[tuple[float, float]] = []
    for ring in (0, 1):
        offset = pitch * (1.5 + 2.0 * ring)
        cx0, cy0 = content[0] - offset, content[1] - offset
        cx1, cy1 = content[2] + offset, content[3] + offset
        cx0, cy0 = max(minx + pitch / 2, cx0), max(miny + pitch / 2, cy0)
        cx1, cy1 = min(maxx - pitch / 2, cx1), min(maxy - pitch / 2, cy1)
        if cx1 <= cx0 or cy1 <= cy0:
            continue
        # top and bottom rows first (the conventional place for a dimension balloon band),
        # then the two sides.
        y_positions = [cy1, cy0]
        for y in y_positions:
            x = cx0
            while x <= cx1 + 1e-9:
                out.append((round(x, 6), round(y, 6)))
                x += pitch
        for x_pos in [cx0, cx1]:
            y = cy0 + pitch
            while y < cy1 - pitch / 2:
                out.append((round(x_pos, 6), round(y, 6)))
                y += pitch
    seen: set[tuple[float, float]] = set()
    ordered: list[tuple[float, float]] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered


def layout_balloons(characteristics: list[Characteristic],
                    extents: tuple[float, float, float, float],
                    balloon_radius: float = 4.0,
                    gap: float = 2.0,
                    margin: float = 14.0) -> Layout:
    """Assign a unique balloon number and a non-overlapping position to each characteristic."""
    if balloon_radius <= 0 or gap <= 0 or margin <= 0:
        raise ValueError("balloon_radius, gap and margin must all be positive")
    minx, miny, maxx, maxy = extents
    if maxx <= minx or maxy <= miny:
        raise ValueError("drawing extents are degenerate; balloon layout is impossible")

    pitch = 2 * balloon_radius + gap
    page = (minx - margin, miny - margin, maxx + margin, maxy + margin)
    slots = _slots(page, extents, pitch)
    if not slots:
        return Layout(page=page, balloons=[], unplaced=[c.cid for c in characteristics],
                      slot_pitch=pitch, ring_offset=margin)

    balloons: list[Balloon] = []
    used: set[int] = set()
    unplaced: list[str] = []

    for c in sorted(characteristics, key=lambda x: x.cid):
        anchor = (c.source.anchor_x, c.source.anchor_y) if c.source else (minx, miny)
        order = sorted(range(len(slots)),
                       key=lambda i: (math.dist(slots[i], anchor), i))
        placed = False
        for i in order:
            centre = slots[i]
            if i in used:
                continue
            # leaders must not run through somebody else's balloon
            blocked = any(
                _seg_circle_hit(anchor, centre, b.centre, b.radius + gap / 2)
                for b in balloons
                if math.dist(b.centre, centre) > 1e-9)
            if blocked:
                continue
            used.add(i)
            number = len(balloons) + 1
            direction = (centre[0] - anchor[0], centre[1] - anchor[1])
            norm = math.hypot(*direction) or 1.0
            edge = (centre[0] - direction[0] / norm * balloon_radius,
                    centre[1] - direction[1] / norm * balloon_radius)
            balloons.append(Balloon(cid=c.cid, number=number, anchor=anchor, centre=centre,
                                   radius=balloon_radius, leader=[anchor, edge]))
            c.balloon_no = number
            placed = True
            break
        if not placed:
            unplaced.append(c.cid)

    return Layout(page=page, balloons=balloons, unplaced=unplaced, slot_pitch=pitch,
                  ring_offset=margin)


def overlaps(balloons: list[Balloon]) -> list[tuple[str, str]]:
    """Report any pair of balloons whose circles intersect. Empty means the layout holds."""
    bad: list[tuple[str, str]] = []
    for i, a in enumerate(balloons):
        for b in balloons[i + 1:]:
            if math.dist(a.centre, b.centre) < a.radius + b.radius - 1e-9:
                bad.append((a.cid, b.cid))
    return bad


def inside_content(balloons: list[Balloon], extents: tuple[float, float, float, float
                                                        ]) -> list[str]:
    """Balloons sitting on top of the part outline obscure the drawing. Report them."""
    minx, miny, maxx, maxy = extents
    return [b.cid for b in balloons
            if minx - b.radius <= b.centre[0] <= maxx + b.radius
            and miny - b.radius <= b.centre[1] <= maxy + b.radius]
