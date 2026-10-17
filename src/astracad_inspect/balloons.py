"""Balloon numbering and layout.

Two placement strategies, chosen per characteristic:

* **Adjacent** (preferred). A balloon sits in clear paper right next to the callout it numbers,
  with a short leader to the callout's text - how an inspector balloons a print by hand. "Clear"
  is measured, not guessed: the sheet is rendered first and its ink map (`Occupancy`) says where
  the paper is already used. A balloon may not cover drawing ink, another callout's text, or
  another balloon, and must stay on the sheet.
* **Ring** (fallback). When no clear spot exists near the callout - or when there is no ink map -
  the balloon goes on a lattice of slots around the drawn content, where non-overlap is a
  property of the lattice spacing.

After placement, leaders that cross are uncrossed by swapping the two balloons' positions: for
straight leaders a swap of a crossing pair always shortens their total length (triangle
inequality), so the pass terminates, and the set of occupied positions - hence every overlap and
ink guarantee - is unchanged by it.

Numbers are then given in reading order: page by page, top band to bottom band, left to right
(ring layouts: clockwise from top-left). Everything is deterministic - ties break on cid and on
candidate index - so the same drawing always produces the same layout.

A characteristic that finds no position at all is reported as unplaced, never stacked.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .model import Characteristic

MM = 1.0
Box = tuple[float, float, float, float]


@dataclass
class Balloon:
    cid: str
    number: int
    anchor: tuple[float, float]          # where the leader starts (on the callout, or the feature)
    centre: tuple[float, float]
    radius: float
    leader: list[tuple[float, float]] = field(default_factory=list)
    page: int = 1
    style: str = "ring"                  # "adjacent" | "ring"
    zone: str = ""


@dataclass
class Layout:
    page: tuple[float, float, float, float]     # page-1 frame: minx, miny, maxx, maxy
    balloons: list[Balloon] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)
    slot_pitch: float = 0.0
    ring_offset: float = 0.0
    pages: dict[int, tuple[float, float, float, float]] = field(default_factory=dict)
    method: str = ""


# ---- geometry helpers ------------------------------------------------------------------------

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


def segments_cross(p1, p2, p3, p4) -> bool:
    """Proper crossing of two segments (touching at an end does not count)."""
    def o(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)
    return (o(p1, p2, p3) * o(p1, p2, p4) < 0) and (o(p3, p4, p1) * o(p3, p4, p2) < 0)


def _nearest_on_box(box: Box, p: tuple[float, float]) -> tuple[float, float]:
    return (min(max(p[0], box[0]), box[2]), min(max(p[1], box[1]), box[3]))


def _circle_box_hit(c: tuple[float, float], r: float, box: Box) -> bool:
    q = _nearest_on_box(box, c)
    return math.dist(q, c) < r


def _leader(target, centre, radius) -> tuple[tuple[float, float], list[tuple[float, float]]]:
    """Leader from the target (a box or a point) to the balloon's edge. Returns (start, points)."""
    start = _nearest_on_box(target, centre) if len(target) == 4 else target
    d = (centre[0] - start[0], centre[1] - start[1])
    n = math.hypot(*d) or 1.0
    edge = (centre[0] - d[0] / n * radius, centre[1] - d[1] / n * radius)
    return start, [start, edge]


# ---- ink map ---------------------------------------------------------------------------------

class Occupancy:
    """Where the rendered sheet already carries ink, in drawing coordinates.

    Built from the balloon-free sheet image the viewer also shows, so "this balloon is on clear
    paper" is a statement about the same picture the user sees. Outside the frame counts as ink,
    which keeps balloons on the sheet."""

    def __init__(self, ink, extents: Box):
        import numpy as np
        self.ext = tuple(float(v) for v in extents)
        self.ink = np.asarray(ink, dtype=bool)
        H, W = self.ink.shape
        x0, y0, x1, y1 = self.ext
        self.sx, self.sy = W / (x1 - x0), H / (y1 - y0)
        ii = np.zeros((H + 1, W + 1), dtype=np.int32)
        ii[1:, 1:] = np.cumsum(np.cumsum(self.ink, axis=0), axis=1)
        self._ii = ii

    @classmethod
    def from_image(cls, path, extents: Box, cell_mm: float = 0.35, threshold: int = 200):
        import numpy as np
        from PIL import Image
        a = np.asarray(Image.open(path).convert("L")) < threshold
        x0, y0, x1, y1 = extents
        px_per_mm = a.shape[1] / (x1 - x0)
        f = max(1, int(round(cell_mm * px_per_mm)))
        H, W = (a.shape[0] // f) * f, (a.shape[1] // f) * f
        a = a[:H, :W].reshape(H // f, f, W // f, f).any(axis=(1, 3))
        # the cropped image no longer spans the full width/height: keep the frame consistent
        ext = (x0, y1 - H / px_per_mm, x0 + W / px_per_mm, y1)
        return cls(a, ext)

    def frac(self, x: float, y: float, r: float) -> float:
        """Share of the disc's bounding square that is ink; off-sheet counts as ink. The square is
        the conservative choice: a line grazing the rim of the disc still counts against it."""
        x0, y0, x1, y1 = self.ext
        h = r
        H, W = self.ink.shape
        c0, c1 = (x - h - x0) * self.sx, (x + h - x0) * self.sx
        r0, r1 = (y1 - (y + h)) * self.sy, (y1 - (y - h)) * self.sy
        area = max(1e-9, (c1 - c0) * (r1 - r0))
        C0, C1 = max(0, int(math.floor(c0))), min(W, int(math.ceil(c1)))
        R0, R1 = max(0, int(math.floor(r0))), min(H, int(math.ceil(r1)))
        if C1 <= C0 or R1 <= R0:
            return 1.0
        inside = (C1 - C0) * (R1 - R0)
        ink = int(self._ii[R1, C1] - self._ii[R0, C1] - self._ii[R1, C0] + self._ii[R0, C0])
        cells = max(1.0, area)
        off = max(0.0, cells - inside)
        return min(1.0, (ink + off) / max(cells, inside))


# ---- ring slots (fallback) -------------------------------------------------------------------

def _slots(page: Box, content: Box, pitch: float, rings: int = 2) -> list[tuple[float, float]]:
    """Rings of candidate centres around the content: top and bottom rows (the conventional
    balloon band), then the sides. More rings are added when a drawing needs them."""
    minx, miny, maxx, maxy = page
    out: list[tuple[float, float]] = []
    for ring in range(rings):
        offset = pitch * (1.5 + 2.0 * ring)
        cx0, cy0 = content[0] - offset, content[1] - offset
        cx1, cy1 = content[2] + offset, content[3] + offset
        cx0, cy0 = max(minx + pitch / 2, cx0), max(miny + pitch / 2, cy0)
        cx1, cy1 = min(maxx - pitch / 2, cx1), min(maxy - pitch / 2, cy1)
        if cx1 <= cx0 or cy1 <= cy0:
            continue
        for y in (cy1, cy0):
            x = cx0
            while x <= cx1 + 1e-9:
                out.append((round(x, 6), round(y, 6)))
                x += pitch
        for x_pos in (cx0, cx1):
            y = cy0 + pitch
            while y < cy1 - pitch / 2:
                out.append((round(x_pos, 6), round(y, 6)))
                y += pitch
    seen: set[tuple[float, float]] = set()
    return [s for s in out if not (s in seen or seen.add(s))]


# ---- placement -------------------------------------------------------------------------------

_DIRS = [(math.cos(math.radians(a)), math.sin(math.radians(a)))
         for a in (90, 0, 180, 270, 45, 135, 315, 225, 22.5, 67.5, 112.5, 157.5,
                   202.5, 247.5, 292.5, 337.5)]


def _target(c: Characteristic) -> Box | tuple[float, float]:
    s = c.source
    tb = getattr(s, "text_box", None) if s else None
    if tb:
        return tuple(float(v) for v in tb)
    return (s.anchor_x, s.anchor_y) if s else (0.0, 0.0)


def _adjacent_candidates(target, occ: Occupancy | None, frame: Box, r: float, gap: float,
                         blockers: list[Box], ink_limit: float):
    """Clear spots next to the callout, cheapest first: (cost, centre)."""
    if occ is None:
        return []
    if len(target) == 4:
        tx0, ty0, tx1, ty1 = target
    else:
        tx0 = tx1 = target[0]
        ty0 = ty1 = target[1]
    cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    hx, hy = (tx1 - tx0) / 2, (ty1 - ty0) / 2
    out = []
    for k, (dx, dy) in enumerate(_DIRS):
        # distance from the box centre to its edge along this direction
        t_edge = min(hx / abs(dx) if abs(dx) > 1e-9 else math.inf,
                     hy / abs(dy) if abs(dy) > 1e-9 else math.inf)
        if t_edge == math.inf:
            t_edge = 0.0
        for extra in (0.0, 0.5, 1.0, 2.0, 3.5, 5.0, 7.0):
            d = t_edge + r + gap * 0.75 + extra * r
            p = (cx + dx * d, cy + dy * d)
            if not (frame[0] + r <= p[0] <= frame[2] - r and frame[1] + r <= p[1] <= frame[3] - r):
                continue
            if math.dist(_nearest_on_box((tx0, ty0, tx1, ty1), p), p) < r + gap * 0.5 - 1e-9:
                continue                    # diagonal spots can clip the callout's own corner
            ink = occ.frac(p[0], p[1], r * 1.08)
            if ink > ink_limit:
                continue
            if any(_circle_box_hit(p, r + gap / 2, b) for b in blockers):
                continue
            lead = max(0.0, math.dist(_nearest_on_box((tx0, ty0, tx1, ty1), p), p) - r)
            # short leaders first; a little preference for the conventional up/right side, and
            # for paper that is entirely clear over paper that only just passes
            cost = lead + 0.15 * r * (k >= 4) + 0.1 * r * (k >= 8) + 6.0 * r * ink
            out.append((cost, k, extra, p))
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    return [(c, p) for c, _, _, p in out]


def _crossings(bs: list[Balloon]) -> int:
    n = 0
    for i in range(len(bs)):
        for j in range(i + 1, len(bs)):
            a, b = bs[i], bs[j]
            if segments_cross(a.leader[0], a.leader[-1], b.leader[0], b.leader[-1]):
                n += 1
    return n


def _uncross(bs: list[Balloon], targets: dict[str, object], max_rounds: int = 400) -> None:
    """Swap the positions of crossing pairs while that strictly reduces the number of crossings
    (or keeps it and shortens the leaders). Positions are only exchanged, never invented."""
    def total(b1, b2):
        return math.dist(b1.leader[0], b1.leader[-1]) + math.dist(b2.leader[0], b2.leader[-1])

    def relead(b):
        b.anchor, b.leader = _leader(targets[b.cid], b.centre, b.radius)

    for _ in range(max_rounds):
        changed = False
        for i in range(len(bs)):
            for j in range(i + 1, len(bs)):
                a, b = bs[i], bs[j]
                if not segments_cross(a.leader[0], a.leader[-1], b.leader[0], b.leader[-1]):
                    continue
                before_x, before_l = _crossings(bs), total(a, b)
                a.centre, b.centre = b.centre, a.centre
                sa, sb = a.style, b.style
                a.style, b.style = sb, sa
                relead(a), relead(b)
                through = any(_seg_circle_hit(x.leader[0], x.leader[-1], o.centre, o.radius * 0.98)
                              for x in (a, b) for o in bs if o is not x)
                after_x = _crossings(bs)
                if not through and (after_x < before_x or (after_x == before_x and total(a, b) < before_l - 1e-9)):
                    changed = True
                else:
                    a.centre, b.centre = b.centre, a.centre
                    a.style, b.style = sa, sb
                    relead(a), relead(b)
        if not changed:
            return


def _reading_order(bs: list[Balloon], frame: Box, pitch: float) -> list[Balloon]:
    """Top band to bottom band, left to right, by where each CALLOUT is (the leader's start), so
    two callouts on one line read in order whichever side their balloons went; a ring layout
    reads clockwise from top-left."""
    if not bs:
        return bs
    if sum(b.style == "ring" for b in bs) > len(bs) / 2:
        cx = (frame[0] + frame[2]) / 2
        cy = (frame[1] + frame[3]) / 2
        start = math.atan2(frame[3] - cy, frame[0] - cx)          # direction of the top-left corner
        def key(b):
            a = math.atan2(b.centre[1] - cy, b.centre[0] - cx)
            return ((start - a) % (2 * math.pi), b.cid)            # clockwise from top-left
        return sorted(bs, key=key)
    band = max((frame[3] - frame[1]) / 8.0, 3 * pitch)
    return sorted(bs, key=lambda b: (int((frame[3] - b.anchor[1]) // band), b.anchor[0], b.cid))


def layout_balloons(characteristics: list[Characteristic],
                    extents: Box,
                    balloon_radius: float = 4.0,
                    gap: float = 2.0,
                    margin: float = 14.0,
                    *,
                    frames: dict[int, Box] | None = None,
                    occupancy: dict[int, Occupancy] | None = None,
                    avoid: dict[int, list[Box]] | None = None,
                    ink_limit: float = 0.02) -> Layout:
    """Assign a unique number and a non-overlapping position to each characteristic.

    `extents` is the drawn content (page 1); `frames` the sheet frame per page (balloons stay
    inside it); `occupancy` the ink map per page; `avoid` other text to keep clear of (escalated
    annotations). Without `occupancy` every balloon goes on the ring, as before."""
    if balloon_radius <= 0 or gap <= 0 or margin <= 0:
        raise ValueError("balloon_radius, gap and margin must all be positive")
    minx, miny, maxx, maxy = extents
    if maxx <= minx or maxy <= miny:
        raise ValueError("drawing extents are degenerate; balloon layout is impossible")
    r = balloon_radius
    pitch = 2 * r + gap
    page1 = (minx - margin, miny - margin, maxx + margin, maxy + margin)
    frames = dict(frames or {})
    frames.setdefault(1, page1)
    occupancy = occupancy or {}
    avoid = avoid or {}

    by_page: dict[int, list[Characteristic]] = {}
    for c in sorted(characteristics, key=lambda x: x.cid):
        by_page.setdefault(int(getattr(c.source, "page", 1) or 1) if c.source else 1, []).append(c)

    balloons: list[Balloon] = []
    unplaced: list[str] = []
    for pno in sorted(by_page):
        chars = by_page[pno]
        frame = frames.get(pno) or frames[1]
        content = extents if pno == 1 else (frame[0] + margin, frame[1] + margin,
                                             frame[2] - margin, frame[3] - margin)
        occ = occupancy.get(pno)
        targets = {c.cid: _target(c) for c in chars}
        text_boxes = {cid: t for cid, t in targets.items() if len(t) == 4}
        placed: list[Balloon] = []

        def free(p, own_target, cid) -> bool:
            if any(math.dist(p, b.centre) < 2 * r + gap - 1e-9 for b in placed):
                return False
            start, lead = _leader(own_target, p, r)
            # the new leader may not run through a placed balloon, and the new balloon may not sit
            # on a placed leader
            if any(_seg_circle_hit(lead[0], lead[-1], b.centre, r + gap / 4) for b in placed):
                return False
            if any(_seg_circle_hit(b.leader[0], b.leader[-1], p, r + gap / 4) for b in placed):
                return False
            return True

        # 1) adjacent: most constrained characteristic first
        cands = {}
        for c in chars:
            blockers = [b for cid, b in text_boxes.items() if cid != c.cid] + list(avoid.get(pno, []))
            cands[c.cid] = _adjacent_candidates(targets[c.cid], occ, frame, r, gap, blockers, ink_limit)
        ring_needed: list[Characteristic] = []
        for c in sorted(chars, key=lambda c: (len(cands[c.cid]) == 0, len(cands[c.cid]), c.cid)):
            spot = None
            best = None
            for cost, p in cands[c.cid][:60]:
                if not free(p, targets[c.cid], c.cid):
                    continue
                _, lead = _leader(targets[c.cid], p, r)
                x = sum(segments_cross(lead[0], lead[-1], b.leader[0], b.leader[-1]) for b in placed)
                score = cost + 8.0 * r * x
                if best is None or score < best:
                    best, spot = score, p
                if x == 0:
                    break
            if spot is None:
                ring_needed.append(c)
                continue
            start, lead = _leader(targets[c.cid], spot, r)
            placed.append(Balloon(c.cid, 0, start, spot, r, lead, pno, "adjacent"))

        # 2) ring for the rest: rings are added until there are enough free slots
        if ring_needed:
            rings = 2
            while True:
                slots = _slots(frame if pno != 1 else (min(frame[0], page1[0]), min(frame[1], page1[1]),
                                                       max(frame[2], page1[2]), max(frame[3], page1[3])),
                               content, pitch, rings)
                free_slots = [s for s in slots if free(s, s, "")]
                if len(free_slots) >= 1.3 * len(ring_needed) or rings >= 6:
                    break
                rings += 1
            ring_targets = {c.cid: (c.source.anchor_x, c.source.anchor_y) if c.source else (minx, miny)
                            for c in ring_needed}
            # nearest-first, around the content: anchors sorted by angle so neighbours stay neighbours
            ccx, ccy = (content[0] + content[2]) / 2, (content[1] + content[3]) / 2
            order = sorted(ring_needed, key=lambda c: (math.atan2(ring_targets[c.cid][1] - ccy,
                                                                  ring_targets[c.cid][0] - ccx), c.cid))
            for c in order:
                anchor = targets[c.cid]
                pt = anchor if len(anchor) == 2 else ((anchor[0] + anchor[2]) / 2, (anchor[1] + anchor[3]) / 2)
                for i in sorted(range(len(slots)), key=lambda i: (math.dist(slots[i], pt), i)):
                    s = slots[i]
                    if occ is not None and occ.frac(s[0], s[1], r) > 0.25:
                        continue                       # a ring slot on a border or title block
                    if free(s, anchor, c.cid):
                        start, lead = _leader(anchor, s, r)
                        placed.append(Balloon(c.cid, 0, start, s, r, lead, pno, "ring"))
                        break
                else:
                    unplaced.append(c.cid)

        _uncross(placed, targets)
        placed = _reading_order(placed, frame, pitch)
        balloons.extend(placed)

    by_cid = {c.cid: c for c in characteristics}
    for n, b in enumerate(balloons, start=1):
        b.number = n
        c = by_cid[b.cid]
        c.balloon_no = n
        if c.source and c.source.zone:
            b.zone = c.source.zone
    for cid in unplaced:
        by_cid[cid].balloon_no = None
    method = ("adjacent to each callout on clear paper (measured from the rendered sheet), ring "
              "fallback; leaders uncrossed; numbered in reading order") if occupancy else \
             ("ring of slots around the content; leaders uncrossed; numbered clockwise")
    return Layout(page=frames.get(1, page1), balloons=balloons, unplaced=sorted(unplaced),
                  slot_pitch=pitch, ring_offset=margin, pages=frames, method=method)


def overlaps(balloons: list[Balloon]) -> list[tuple[str, str]]:
    """Report any pair of balloons on the same page whose circles intersect."""
    bad: list[tuple[str, str]] = []
    for i, a in enumerate(balloons):
        for b in balloons[i + 1:]:
            if a.page == b.page and math.dist(a.centre, b.centre) < a.radius + b.radius - 1e-9:
                bad.append((a.cid, b.cid))
    return bad


def leader_crossings(balloons: list[Balloon]) -> list[tuple[str, str]]:
    bad = []
    for i, a in enumerate(balloons):
        for b in balloons[i + 1:]:
            if a.page == b.page and segments_cross(a.leader[0], a.leader[-1], b.leader[0], b.leader[-1]):
                bad.append((a.cid, b.cid))
    return bad


def inside_content(balloons: list[Balloon], extents: tuple[float, float, float, float
                                                        ]) -> list[str]:
    """Ring layouts: balloons sitting on top of the part outline obscure the drawing."""
    minx, miny, maxx, maxy = extents
    return [b.cid for b in balloons
            if b.page == 1 and b.style == "ring"
            and minx - b.radius <= b.centre[0] <= maxx + b.radius
            and miny - b.radius <= b.centre[1] <= maxy + b.radius]


def on_ink(balloons: list[Balloon], occupancy: dict[int, Occupancy], limit: float = 0.03) -> list[str]:
    """Balloons whose disc covers drawing ink, measured on the rendered sheet."""
    return [b.cid for b in balloons
            if b.page in occupancy and occupancy[b.page].frac(b.centre[0], b.centre[1], b.radius) > limit]
