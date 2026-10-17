"""Revision intelligence: compare two inspection plans of the same part (constitution Part 11).

Matching does not rely on entity handles (a re-saved drawing renumbers them) or on raw
coordinates (a revised sheet may move the view). Each characteristic is described by

- its kind (linear / diameter / radius / angular ...),
- its definition points expressed relative to the drawing's content extents (translation- and
  scale-normalised), and
- the geometry value measured from those points,

and paired by a score over those features. The callout text is *not* used to pair, because the
callout is exactly what a revision changes; it is compared after pairing.

Outcomes per pair: UNCHANGED, MOVED (same requirement, new position), CHANGED_NOMINAL,
CHANGED_TOLERANCE, CHANGED_COUNT, CHANGED_CALLOUT (text differs, limits identical), plus ADDED,
REMOVED and AMBIGUOUS (two or more equally good candidates — never resolved by guessing).
Every consequential change carries ``requires_review = True``; nothing here approves a change.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONSEQUENTIAL = {"CHANGED_NOMINAL", "CHANGED_TOLERANCE", "CHANGED_COUNT", "ADDED", "REMOVED",
                 "AMBIGUOUS"}
MATCH_LIMIT = 0.08       # normalised position distance beyond which two features are unrelated
AMBIGUITY_MARGIN = 0.02  # runner-up within this of the best -> ambiguous


@dataclass
class Change:
    status: str
    old_cid: str | None
    new_cid: str | None
    kind: str
    old: dict | None
    new: dict | None
    detail: str
    score: float | None = None
    requires_review: bool = False


@dataclass
class ChangeSet:
    old_document: str
    new_document: str
    old_revision: str | None
    new_revision: str | None
    old_sha256: str | None
    new_sha256: str | None
    changes: list[Change] = field(default_factory=list)
    notes_added: list[str] = field(default_factory=list)
    notes_removed: list[str] = field(default_factory=list)
    title_block_changes: dict = field(default_factory=dict)
    method: str = ("pairing by kind + extents-normalised definition points + measured geometry; "
                   "callout text compared only after pairing")

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.changes:
            out[c.status] = out.get(c.status, 0) + 1
        return out

    @property
    def needs_review(self) -> bool:
        return any(c.requires_review for c in self.changes) or bool(
            self.notes_added or self.notes_removed)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["summary"] = self.summary()
        d["needs_review"] = self.needs_review
        return d


def _frame(plan: dict) -> tuple[float, float, float]:
    ext = plan.get("content_extents") or plan.get("extents")
    if not ext:
        return 0.0, 0.0, 1.0
    x0, y0, x1, y1 = ext
    return x0, y0, math.hypot(x1 - x0, y1 - y0) or 1.0


def _signature(ch: dict, frame) -> dict:
    x0, y0, diag = frame
    src = ch.get("source") or {}
    pts = src.get("definition_points") or []
    if not pts and src.get("anchor_x") is not None:
        pts = [[src["anchor_x"], src["anchor_y"]]]
    norm = [((p[0] - x0) / diag, (p[1] - y0) / diag) for p in pts]
    return {"kind": ch.get("kind"), "pts": norm, "geom": ch.get("geometry_value"),
            "diag": diag}


def _pts_distance(a: list, b: list) -> float:
    if not a or not b:
        return math.inf
    # symmetric mean nearest-point distance: order of definition points is not significant
    def one(p, q):
        return sum(min(math.dist(x, y) for y in q) for x in p) / len(p)
    return (one(a, b) + one(b, a)) / 2


def _score(sa: dict, sb: dict) -> float:
    if sa["kind"] != sb["kind"]:
        return math.inf
    d = _pts_distance(sa["pts"], sb["pts"])
    ga, gb = sa["geom"], sb["geom"]
    if ga is not None and gb is not None:
        # a geometry change shifts the points too; weigh relative size difference lightly
        d += 0.25 * abs(ga - gb) / max(abs(ga), abs(gb), 1e-9) * 0.1
    return d


def _req(ch: dict) -> dict:
    return {"callout": ch.get("raw_callout"), "nominal": ch.get("nominal"),
            "tolerance": ch.get("tolerance"), "limits": ch.get("limits"),
            "count": ch.get("count"), "unit": ch.get("unit"),
            "geometry_value": ch.get("geometry_value"),
            "handle": (ch.get("source") or {}).get("entity_handle"),
            "position": [(ch.get("source") or {}).get("anchor_x"),
                         (ch.get("source") or {}).get("anchor_y")]}


def _classify(o: dict, n: dict, pos_d: float) -> tuple[str, str]:
    ro, rn = _req(o), _req(n)
    diffs = []
    if ro["nominal"] != rn["nominal"]:
        diffs.append(("CHANGED_NOMINAL", f"nominal {ro['nominal']} -> {rn['nominal']}"))
    if ro["tolerance"] != rn["tolerance"] or ro["limits"] != rn["limits"]:
        diffs.append(("CHANGED_TOLERANCE",
                      f"tolerance {ro['tolerance']} (limits {ro['limits']}) -> "
                      f"{rn['tolerance']} (limits {rn['limits']})"))
    if ro["count"] != rn["count"]:
        diffs.append(("CHANGED_COUNT", f"count {ro['count']} -> {rn['count']}"))
    if diffs:
        return diffs[0][0], "; ".join(d for _, d in diffs)
    if ro["callout"] != rn["callout"]:
        return "CHANGED_CALLOUT", f"text {ro['callout']!r} -> {rn['callout']!r}, limits identical"
    if pos_d > 0.005:
        return "MOVED", f"same requirement, position shifted by {pos_d:.3f} of the extents diagonal"
    return "UNCHANGED", "identical requirement and position"


def compare_plans(old: dict, new: dict) -> ChangeSet:
    # normalise through JSON so a plan read from disk (lists) and a plan built in memory (tuples)
    # compare equal when their values are equal
    old = json.loads(json.dumps(old, default=str))
    new = json.loads(json.dumps(new, default=str))
    cs = ChangeSet(old.get("source_document"), new.get("source_document"), old.get("revision"),
                   new.get("revision"), old.get("source_sha256"), new.get("source_sha256"))
    fo, fn = _frame(old), _frame(new)
    olds = [(c, _signature(c, fo)) for c in old.get("characteristics", [])]
    news = [(c, _signature(c, fn)) for c in new.get("characteristics", [])]

    pairs = []
    for i, (_, so) in enumerate(olds):
        for j, (_, sn) in enumerate(news):
            s = _score(so, sn)
            if s <= MATCH_LIMIT:
                pairs.append((s, i, j))
    pairs.sort()
    used_o, used_n, ambiguous_o = set(), set(), set()
    # an old feature whose best two candidates are equally close is ambiguous
    for i in range(len(olds)):
        cand = sorted(s for s, a, _ in pairs if a == i)
        if len(cand) > 1 and cand[1] - cand[0] < AMBIGUITY_MARGIN and cand[0] > 1e-6:
            ambiguous_o.add(i)
    for s, i, j in pairs:
        if i in used_o or j in used_n or i in ambiguous_o:
            continue
        used_o.add(i)
        used_n.add(j)
        o, n = olds[i][0], news[j][0]
        status, detail = _classify(o, n, s)
        cs.changes.append(Change(status, o["cid"], n["cid"], o.get("kind"), _req(o), _req(n),
                                 detail, round(s, 6), status in CONSEQUENTIAL))
    for i in sorted(ambiguous_o):
        o = olds[i][0]
        cands = [news[j][0]["cid"] for s, a, j in pairs if a == i]
        cs.changes.append(Change("AMBIGUOUS", o["cid"], None, o.get("kind"), _req(o), None,
                                 f"equally close candidates in the new revision: {cands}; "
                                 "a reviewer must pair them", None, True))
        used_o.add(i)
    for i, (o, _) in enumerate(olds):
        if i not in used_o:
            cs.changes.append(Change("REMOVED", o["cid"], None, o.get("kind"), _req(o), None,
                                     "no feature of this kind near this position in the new "
                                     "revision", None, True))
    for j, (n, _) in enumerate(news):
        if j not in used_n:
            cs.changes.append(Change("ADDED", None, n["cid"], n.get("kind"), None, _req(n),
                                     "no counterpart in the old revision", None, True))
    on, nn = set(old.get("notes") or []), set(new.get("notes") or [])
    cs.notes_added, cs.notes_removed = sorted(nn - on), sorted(on - nn)
    tbo, tbn = old.get("title_block") or {}, new.get("title_block") or {}
    cs.title_block_changes = {k: [tbo.get(k), tbn.get(k)] for k in sorted(set(tbo) | set(tbn))
                              if not k.startswith("_") and tbo.get(k) != tbn.get(k)}
    order = {"REMOVED": 0, "ADDED": 1, "AMBIGUOUS": 2, "CHANGED_NOMINAL": 3,
             "CHANGED_TOLERANCE": 4, "CHANGED_COUNT": 5, "CHANGED_CALLOUT": 6, "MOVED": 7,
             "UNCHANGED": 8}
    cs.changes.sort(key=lambda c: (order[c.status], c.old_cid or "", c.new_cid or ""))
    return cs


def compare_drawings(old_dxf: str | Path, new_dxf: str | Path, *, unit: str | None = None,
                     old_revision: str | None = None, new_revision: str | None = None) -> ChangeSet:
    from .pipeline import read_drawing
    from .plan import build_plan
    a = build_plan(read_drawing(Path(old_dxf), unit, old_revision)[0]).to_dict()
    b = build_plan(read_drawing(Path(new_dxf), unit, new_revision)[0]).to_dict()
    return compare_plans(a, b)


def write_changeset(cs: ChangeSet, out_dir: str | Path, stem: str) -> dict[str, Path]:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    js = out / f"{stem}.changes.json"
    js.write_text(json.dumps(cs.to_dict(), indent=2, ensure_ascii=False, default=str),
                  encoding="utf-8")
    wb = Workbook()
    ws = wb.active
    ws.title = "Changes"
    head = ["Status", "Review", "Kind", "Old ID", "New ID", "Old callout", "New callout",
            "Old limits", "New limits", "Detail"]
    ws.append(head)
    for c in head:
        pass
    for cell in ws[1]:
        cell.font = Font(bold=True)
    fill = PatternFill("solid", fgColor="FFF2CC")
    for c in cs.changes:
        ws.append([c.status, "REQUIRED" if c.requires_review else "", c.kind, c.old_cid,
                   c.new_cid, (c.old or {}).get("callout"), (c.new or {}).get("callout"),
                   str((c.old or {}).get("limits")), str((c.new or {}).get("limits")), c.detail])
        if c.requires_review:
            for cell in ws[ws.max_row]:
                cell.fill = fill
    meta = wb.create_sheet("Provenance")
    for k in ("old_document", "old_revision", "old_sha256", "new_document", "new_revision",
              "new_sha256", "method"):
        meta.append([k, str(getattr(cs, k))])
    meta.append(["notes_added", "; ".join(cs.notes_added)])
    meta.append(["notes_removed", "; ".join(cs.notes_removed)])
    meta.append(["title_block_changes", json.dumps(cs.title_block_changes, ensure_ascii=False)])
    meta.append(["approval", "NOT APPROVED — this change set is a review input, not a decision"])
    xl = out / f"{stem}.changes.xlsx"
    wb.save(xl)
    return {"json": js, "xlsx": xl}
