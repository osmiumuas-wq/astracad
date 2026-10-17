"""Write a vector PDF of a DXF fixture the way a CAD "Save as PDF" does: geometry as paths, every
callout as real (selectable) text, symbols as glyphs (Ø ± °), and deviation tolerances stacked
as two smaller text runs to the right of the nominal — the layout most CAD exporters produce.

SYNTHETIC: this is a test fixture, not a real drawing. It exists so the PDF route can be scored
against the same expected characteristics as the DXF it was drawn from.

    py tools/make_pdf_fixture.py tests/fixtures/plate_synthetic.dxf tests/fixtures/plate_synthetic.pdf
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import ezdxf
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from astracad_inspect.callouts import normalise  # noqa: E402
from astracad_inspect.ingest.dxf import _callout_text  # noqa: E402

OFFSET = (40.0, 60.0)          # drawing mm -> page mm (scale 1:1)
TEXT_H = 3.0
_STACK = re.compile(r"^(?P<nom>.*?\d)\s*(?P<up>[+-]\d+(?:\.\d+)?)\s*/\s*(?P<lo>[+-]?\d+(?:\.\d+)?)$")


def _p(x: float, y: float) -> tuple[float, float]:
    return ((x + OFFSET[0]) * mm, (y + OFFSET[1]) * mm)


def _glyphs(text: str) -> str:
    # Helvetica (WinAnsi) has Ø, ± and °; the reader is expected to map Ø back to ⌀.
    return normalise(text).replace("⌀", "Ø")


def _text(c: canvas.Canvas, x: float, y: float, s: str, angle: float, h: float = TEXT_H) -> None:
    c.saveState()
    c.translate(*_p(x, y))
    c.rotate(angle)
    m = _STACK.match(s)
    c.setFont("Helvetica", h * mm / 0.72 * 0.72)
    if m and "/" in s and "±" not in s:
        nom = m.group("nom")
        w = c.stringWidth(nom, "Helvetica", h * mm)
        c.setFont("Helvetica", h * mm)
        c.drawString(-w / 2, -h * mm / 2, nom)
        c.setFont("Helvetica", h * 0.6 * mm)
        lo = m.group("lo") if m.group("lo").startswith(("+", "-")) else "-" + m.group("lo")
        c.drawString(-w / 2 + w + 0.6 * mm, 0.05 * mm, m.group("up"))
        c.drawString(-w / 2 + w + 0.6 * mm, -h * 0.6 * mm - 0.25 * mm, lo)
    else:
        c.setFont("Helvetica", h * mm)
        w = c.stringWidth(s, "Helvetica", h * mm)
        c.drawString(-w / 2, -h * mm / 2, s)
    c.restoreState()


def make(dxf: Path, out: Path) -> Path:
    doc = ezdxf.readfile(str(dxf))
    msp = doc.modelspace()
    c = canvas.Canvas(str(out), pagesize=landscape(A4))
    c.setTitle(f"{dxf.stem} (SYNTHETIC PDF FIXTURE)")
    c.setLineWidth(0.35 * mm)
    for e in msp:
        t = e.dxftype()
        if t == "LINE":
            c.line(*_p(e.dxf.start.x, e.dxf.start.y), *_p(e.dxf.end.x, e.dxf.end.y))
        elif t == "LWPOLYLINE":
            pts = [_p(x, y) for x, y, *_ in e.get_points()]
            if e.closed:
                pts.append(pts[0])
            for a, b in zip(pts, pts[1:]):
                c.line(*a, *b)
        elif t == "CIRCLE":
            c.circle(*_p(e.dxf.center.x, e.dxf.center.y), e.dxf.radius * mm)
        elif t == "ARC":
            cx, cy, r = e.dxf.center.x, e.dxf.center.y, e.dxf.radius
            a0, a1 = e.dxf.start_angle, e.dxf.end_angle
            ext = (a1 - a0) % 360 or 360
            x0, y0 = _p(cx - r, cy - r)
            x1, y1 = _p(cx + r, cy + r)
            c.arc(x0, y0, x1, y1, a0, ext)
        elif t in ("TEXT", "MTEXT"):
            s = e.dxf.text if t == "TEXT" else e.text
            ins = e.dxf.insert
            c.setFont("Helvetica", e.dxf.get("height", TEXT_H) * mm)
            c.drawString(*_p(ins.x, ins.y), _glyphs(s))
    c.setLineWidth(0.18 * mm)
    for e in msp:
        if e.dxftype() != "DIMENSION":
            continue
        text, _ = _callout_text(doc, e)
        mid = e.dxf.get("text_midpoint")
        angle = float(e.dxf.get("angle", 0.0) or 0.0) if (int(e.dxf.dimtype) & 7) in (0, 1) else 0.0
        if mid is None:
            continue
        # A short dimension line under the text, as a CAD export would draw it.
        dx, dy = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        ox, oy = -dy * (TEXT_H * 0.9), dx * (TEXT_H * 0.9)
        c.line(*_p(mid.x - 8 * dx - ox, mid.y - 8 * dy - oy), *_p(mid.x + 8 * dx - ox, mid.y + 8 * dy - oy))
        _text(c, mid.x, mid.y, _glyphs(text), angle)
    c.showPage()
    c.save()
    return out


if __name__ == "__main__":
    print(make(Path(sys.argv[1]), Path(sys.argv[2])))
