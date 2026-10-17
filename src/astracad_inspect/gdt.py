"""Feature control frames (GD&T) — ISO 1101 / ASME Y14.5 notation.

Two spellings are read:

* the DXF ``TOLERANCE`` entity content written by AutoCAD-family exporters, where symbols are
  letters in the ``gdt`` font (``{\\Fgdt;j}`` = position), boxes are separated by ``%%v`` and
  frame rows by ``^J``;
* plain Unicode text (``⌖ ⌀0.01 Ⓜ | A | B | C``), as found in PDF text or OCR output.

Each frame row is one characteristic. A row is resolved only when its symbol is recognised and
its first box is a single tolerance value; anything else is returned with the reason, never
filled in. The tolerance zone is a width, so the inspectable limits are 0 .. zone. A material
modifier (Ⓜ / Ⓛ) allows bonus tolerance that depends on the measured feature size; that bonus
is NOT computed here and the row says so.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# gdt-font letter -> characteristic (AutoCAD GDT.SHX / gdt.ttf mapping)
GDT_FONT = {
    "a": "angularity", "b": "perpendicularity", "c": "flatness", "d": "profile of a surface",
    "e": "circularity", "f": "parallelism", "g": "cylindricity", "h": "circular runout",
    "i": "symmetry", "j": "position", "k": "profile of a line", "r": "concentricity",
    "t": "total runout", "u": "straightness",
}
# gdt-font letter -> modifier inside a box
GDT_MODIFIERS = {"m": "MMC", "l": "LMC", "s": "RFS", "p": "projected", "t": "tangent plane",
                 "f": "free state"}
UNICODE_SYMBOLS = {
    "⌖": "position", "⊥": "perpendicularity", "∥": "parallelism", "∠": "angularity",
    "⏥": "flatness", "○": "circularity", "◯": "circularity", "⌭": "cylindricity",
    "⌒": "profile of a line", "⌓": "profile of a surface", "↗": "circular runout",
    "⌰": "total runout", "◎": "concentricity", "⌯": "symmetry", "—": "straightness",
    "⏤": "straightness",
}
UNICODE_MODIFIERS = {"Ⓜ": "MMC", "Ⓛ": "LMC", "Ⓢ": "RFS", "Ⓟ": "projected", "Ⓣ": "tangent plane",
                     "Ⓕ": "free state"}
# Characteristics that never take a datum (ISO 1101 form tolerances).
FORM = {"flatness", "circularity", "cylindricity", "straightness"}
_NUM = re.compile(r"^\d*\.?\d+$")


@dataclass(frozen=True)
class GeometricTolerance:
    """One frame row. `zone` is the tolerance-zone width in drawing units."""
    symbol: str
    zone: float
    diameter_zone: bool = False
    modifier: str | None = None
    datums: tuple[str, ...] = ()
    kind: str = "geometric"


@dataclass
class FrameParse:
    raw: str
    tolerance: GeometricTolerance | None = None
    unresolved_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return self.tolerance is not None


def describe(t: GeometricTolerance) -> str:
    zone = ("⌀" if t.diameter_zone else "") + f"{t.zone:g}"
    if t.modifier:
        zone += f" ({t.modifier})"
    return " | ".join([t.symbol, zone, *t.datums])


def _row_from_boxes(raw: str, symbol: str | None, boxes: list[str],
                    box_mods: list[list[str]], diameter: bool) -> FrameParse:
    fp = FrameParse(raw)
    if symbol is None:
        fp.unresolved_reason = "the characteristic symbol is missing or not recognised"
        return fp
    if not boxes:
        fp.unresolved_reason = f"{symbol}: no tolerance value box"
        return fp
    value = boxes[0].strip()
    if not _NUM.match(value):
        fp.unresolved_reason = f"{symbol}: tolerance box {value!r} is not a single number"
        return fp
    mods = box_mods[0] if box_mods else []
    if len(mods) > 1:
        fp.notes.append(f"several modifiers in the tolerance box ({', '.join(mods)}); the first "
                        "material condition is kept, the rest are recorded here")
    modifier = next((m for m in mods if m in ("MMC", "LMC", "RFS")), mods[0] if mods else None)
    datums = []
    for i, d in enumerate(boxes[1:], start=1):
        d = d.strip()
        if not d:
            continue
        if not re.match(r"^[A-Z](?:-[A-Z])?$", d):
            fp.unresolved_reason = f"{symbol}: datum box {d!r} is not a datum letter"
            return fp
        dm = box_mods[i] if i < len(box_mods) else []
        datums.append(d + (f"({dm[0]})" if dm else ""))
    if symbol in FORM and datums:
        fp.notes.append(f"{symbol} is a form tolerance and takes no datum, yet datums "
                        f"{datums} are written; recorded as written")
    if symbol == "position" and not datums:
        fp.notes.append("position with no datum reference frame")
    fp.tolerance = GeometricTolerance(symbol, float(value), diameter, modifier, tuple(datums))
    return fp


def parse_dxf_tolerance(content: str) -> list[FrameParse]:
    """Parse a DXF TOLERANCE entity's content into one result per frame row."""
    out = []
    for row in (content or "").split("^J"):
        if not row.strip():
            continue
        boxes_raw = row.split("%%v")
        symbol = None
        first = boxes_raw[0]
        m = re.search(r"\{\\Fgdt;([a-z])\}", first, re.I)
        if m:
            symbol = GDT_FONT.get(m.group(1).lower())
        elif first.strip() == "":
            symbol = None
        boxes, mods, diameter = [], [], False
        for b in boxes_raw[1:]:
            box_mods = []
            for code in re.findall(r"\{\\Fgdt;([a-z])\}", b, re.I):
                c = code.lower()
                if c == "n":
                    if not boxes:
                        diameter = True
                    continue
                if c in GDT_MODIFIERS:
                    box_mods.append(GDT_MODIFIERS[c])
            text = re.sub(r"\{\\[^}]*\}", "", b).replace("%%c", "").strip()
            if "%%c" in b and not boxes:
                diameter = True
            boxes.append(text)
            mods.append(box_mods)
        while boxes and not boxes[-1]:        # trailing empty datum boxes
            boxes.pop()
            mods.pop()
        out.append(_row_from_boxes(row, symbol, boxes, mods, diameter))
    return out


def parse_unicode_frame(text: str) -> FrameParse | None:
    """Parse '⌖ ⌀0.01 Ⓜ | A | B | C' style text. Returns None when the text is not a frame at all
    (no GD&T symbol), so ordinary callouts are left to the dimension parser."""
    t = (text or "").strip()
    sym_char = next((s for s in UNICODE_SYMBOLS if t.startswith(s)), None)
    if sym_char is None:
        return None
    body = t[len(sym_char):].strip().lstrip("|").strip()
    parts = [p.strip() for p in re.split(r"\s*\|\s*", body) if p.strip()]
    if not parts:
        return _row_from_boxes(text, UNICODE_SYMBOLS[sym_char], [], [], False)
    tol = parts[0]
    diameter = bool(re.match(r"^[⌀Øø∅]", tol))
    tol = re.sub(r"^[⌀Øø∅]\s*", "", tol)
    mods = [UNICODE_MODIFIERS[c] for c in tol if c in UNICODE_MODIFIERS]
    tol = "".join(c for c in tol if c not in UNICODE_MODIFIERS).strip()
    # "0.01 A B C" written without bars: split trailing datum letters off the value
    m = re.match(r"^(\d*\.?\d+)\s+((?:[A-Z]\s*)+)$", tol)
    rest = parts[1:]
    if m:
        tol, rest = m.group(1), m.group(2).split() + rest
    boxes = [tol] + rest
    box_mods = [mods] + [[] for _ in rest]
    return _row_from_boxes(text, UNICODE_SYMBOLS[sym_char], boxes, box_mods, diameter)
