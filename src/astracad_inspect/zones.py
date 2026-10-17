"""Drawing zones (sheet grid references such as "C4") from the sheet's own border labels.

A zone is reported only when the sheet carries both column labels (numbers) and row labels
(letters): the characteristic's zone is the nearest column label by x and the nearest row label by
y. Nothing is inferred from sheet size or a standard grid; without labels the zone stays empty.
"""
from __future__ import annotations


def assign_zones(characteristics, labels) -> int:
    """labels: iterable of (page, text, x, y) in the same coordinates as the characteristics'
    anchors. Returns how many characteristics received a zone."""
    by_page: dict[int, tuple[list, list]] = {}
    for page, text, x, y in labels:
        t = text.strip()
        cols, rows = by_page.setdefault(page, ([], []))
        if t.isdigit():
            cols.append((x, t))
        elif len(t) == 1 and t.isalpha() and t.isupper():
            rows.append((y, t))
    n = 0
    for c in characteristics:
        src = c.source
        if src is None:
            continue
        cols, rows = by_page.get(src.page, ([], []))
        if len({t for _, t in cols}) < 2 or len({t for _, t in rows}) < 2:
            continue
        col = min(cols, key=lambda p: abs(p[0] - src.anchor_x))[1]
        row = min(rows, key=lambda p: abs(p[0] - src.anchor_y))[1]
        src.zone = f"{row}{col}"
        n += 1
    return n
