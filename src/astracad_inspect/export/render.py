"""Render a DXF modelspace with its inspection balloons overlaid.

The drawing is rendered from the same vector source the extraction read, so what the inspector
sees is the geometry the numbers were checked against rather than a separate picture that could
have drifted from it. Balloon numbers are sized from the balloon radius in drawing units, so the
label stays inside its circle whatever the sheet size.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display on this machine, and a headless run must not depend on one
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

from ..balloons import Layout
from ..provenance import file_record


def _ax_limits(page: tuple[float, float, float, float], pad: float) -> tuple:
    return (page[0] - pad, page[2] + pad, page[1] - pad, page[3] + pad)


def _draw_modelspace(msp, ax, ctx) -> tuple[object, list[str]]:
    """Paint the modelspace and hand back the backend that did it, plus the entities ezdxf refused
    to paint.

    A LEADER written without vertex coordinates (which is what LibreCAD emits) makes ezdxf's frontend
    raise partway through - after some entities are already on the axes. The retry therefore clears
    the axes and repaints what it can, so the page shows the rest of the drawing instead of costing
    the report its layout image. What was left out is named, because a preview missing an entity is
    not the same claim as a preview of the drawing."""
    from ezdxf.addons.drawing import Frontend
    from ezdxf.addons.drawing.config import ColorPolicy, Configuration
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
    from ezdxf.protocols import virtual_entities

    def paint(skip: frozenset = frozenset()):
        # Modelspace pens are authored for a dark CAD background. Index 7 is white, which on a white
        # report page renders the part invisible while every check still passes.
        backend = MatplotlibBackend(ax)
        Frontend(ctx, backend, config=Configuration(color_policy=ColorPolicy.BLACK)) \
            .draw_layout(msp, finalize=False,
                         filter_func=lambda e: (e.dxf.handle, e.dxftype()) not in skip)
        return backend

    skip: frozenset = frozenset()
    try:
        return paint(), []
    except ValueError:
        for e in msp:
            try:
                list(virtual_entities(e))
            except ValueError:
                skip = skip | {(e.dxf.handle, e.dxftype())}
            except TypeError:
                pass  # not a composite entity, so there are no virtual ones to fail on
    if not skip:
        raise  # not an entity this can name: let the real cause surface
    ax.clear()
    ax.set_facecolor("white")
    return paint(skip), [f"{t} (handle {h})" for h, t in sorted(skip)]


# ezdxf's drawing add-on does not paint TOLERANCE entities, so a frame would be missing from the
# preview while its balloon points at empty space. Frames are drawn here from the same parse the
# extraction used. Glyphs DejaVu Sans lacks are written as short codes.
_FRAME_GLYPH = {"perpendicularity": "⊥", "parallelism": "∥", "angularity": "∠", "flatness": "⏥",
                "circularity": "○", "circular runout": "↗", "concentricity": "◎",
                "position": "POS", "cylindricity": "CYL", "profile of a line": "PRL",
                "profile of a surface": "PRS", "total runout": "TRO", "symmetry": "SYM",
                "straightness": "STR"}


def _draw_frames(msp, ax) -> int:
    from ..gdt import parse_dxf_tolerance
    drawn = 0
    for e in msp.query("TOLERANCE"):
        x0, y0 = float(e.dxf.insert.x), float(e.dxf.insert.y)
        h = 4.0
        for i, fr in enumerate(parse_dxf_tolerance(e.dxf.get("content", "") or "")):
            y = y0 - i * h
            if fr.tolerance is None:
                cells = ["?", fr.raw[:12]]
            else:
                t = fr.tolerance
                zone = ("⌀" if t.diameter_zone else "") + f"{t.zone:g}"
                if t.modifier:
                    zone += {"MMC": " (M)", "LMC": " (L)", "RFS": " (S)"}.get(t.modifier, "")
                cells = [_FRAME_GLYPH.get(t.symbol, t.symbol[:3].upper()), zone,
                         *[d.replace("(MMC)", "(M)").replace("(LMC)", "(L)") for d in t.datums]]
            x = x0
            for c in cells:
                w = max(h, 0.62 * h * len(c) + 1.2)
                ax.add_patch(Rectangle((x, y - h), w, h, fill=False, linewidth=0.6,
                                       edgecolor="black", zorder=4))
                ax.text(x + w / 2, y - h / 2, c, ha="center", va="center", fontsize=5.5,
                        color="black", zorder=4)
                x += w
            drawn += 1
    return drawn


def render_drawing(dxf_path: str | Path, layout: Layout, out_path: str | Path,
                   *, width_in: float = 9.0, caption: str | None = None,
                   show_anchors: bool = True) -> dict:
    """Write a raster page of the drawing plus balloons and return what was produced."""
    import ezdxf
    from ezdxf.addons.drawing import RenderContext

    src = Path(dxf_path)
    doc = ezdxf.readfile(str(src))
    msp = doc.modelspace()

    minx, maxx, miny, maxy = _ax_limits(layout.page, layout.slot_pitch / 2)
    aspect = (maxy - miny) / (maxx - minx) if maxx > minx else 1.0
    fig = plt.figure(figsize=(width_in, max(2.0, width_in * aspect)))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0.03, 0.03, 0.94, 0.94])
    ax.set_facecolor("white")
    ctx = RenderContext(doc)
    backend, not_drawn = _draw_modelspace(msp, ax, ctx)
    frames_drawn = _draw_frames(msp, ax)

    _overlay(ax, layout, minx, maxx, miny, maxy, caption, show_anchors)
    backend.finalize()

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    dpi = fig.dpi
    fig.savefig(str(out), dpi=dpi, facecolor="white")
    plt.close(fig)

    record = file_record(out)
    record.update({"dpi": int(dpi), "shown_extents": [minx, miny, maxx, maxy],
                   "balloons_drawn": len(layout.balloons),
                   "balloons_not_drawn": list(layout.unplaced),
                   "entities_not_drawn": not_drawn, "frames_drawn": frames_drawn,
                   "ink_fraction": ink_fraction(out)})
    return record


def _overlay(ax, layout: Layout, minx, maxx, miny, maxy, caption, show_anchors) -> None:
    for b in layout.balloons:
        ax.add_patch(Circle(b.centre, b.radius, facecolor="white", edgecolor="#b00020",
                            linewidth=1.1, zorder=6))
        ax.plot(*zip(*b.leader), color="#b00020", linewidth=0.7, zorder=5)
        if show_anchors:
            ax.plot(b.anchor[0], b.anchor[1], marker=".", markersize=2.5,
                    color="#b00020", zorder=5)
        font_pt = min(11.0, max(4.0, b.radius / 25.4 * 72 * 0.8))
        ax.text(b.centre[0], b.centre[1], str(b.number), ha="center", va="center",
                fontsize=font_pt, color="#b00020", zorder=7)
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("equal", adjustable="box")
    ax.set_axis_off()
    if caption:
        ax.set_title(caption, fontsize=8)


def render_pdf_page(pdf_path: str | Path, layout: Layout, out_path: str | Path,
                    *, width_in: float = 9.0, caption: str | None = None,
                    show_anchors: bool = True, dpi_source: float = 200.0) -> dict:
    """Rasterise page 1 of a vector PDF (pdfium) in page millimetres and overlay the balloons.

    The picture is the sheet the callouts were read from, in the same coordinates as the anchors,
    so a balloon's leader points at the text it was read from."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    page = pdf[0]
    w_pt, h_pt = page.get_size()
    img = page.render(scale=dpi_source / 72.0).to_pil().convert("RGB")
    w_mm, h_mm = w_pt * 25.4 / 72, h_pt * 25.4 / 72

    minx, maxx, miny, maxy = _ax_limits(layout.page, layout.slot_pitch / 2)
    aspect = (maxy - miny) / (maxx - minx) if maxx > minx else 1.0
    fig = plt.figure(figsize=(width_in, max(2.0, width_in * aspect)))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0.03, 0.03, 0.94, 0.94])
    ax.set_facecolor("white")
    ax.imshow(img, extent=(0, w_mm, 0, h_mm), origin="upper", interpolation="antialiased",
              zorder=1)
    _overlay(ax, layout, minx, maxx, miny, maxy, caption, show_anchors)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out), dpi=fig.dpi, facecolor="white")
    plt.close(fig)
    record = file_record(out)
    record.update({"dpi": int(fig.dpi), "shown_extents": [minx, miny, maxx, maxy],
                   "source_raster_dpi": dpi_source,
                   "balloons_drawn": len(layout.balloons),
                   "balloons_not_drawn": list(layout.unplaced),
                   "entities_not_drawn": [], "ink_fraction": ink_fraction(out)})
    return record


def render_raster_page(path: str | Path, layout: Layout, out_path: str | Path,
                       *, width_in: float = 9.0, caption: str | None = None,
                       show_anchors: bool = True) -> dict:
    """Page 1 of a scanned drawing (image or image-only PDF) in page millimetres, with balloons."""
    from ..ingest.raster import load_pages
    img, dpi, _ = load_pages(Path(path))[0]
    w_mm, h_mm = img.width * 25.4 / dpi, img.height * 25.4 / dpi
    minx, maxx, miny, maxy = _ax_limits(layout.page, layout.slot_pitch / 2)
    aspect = (maxy - miny) / (maxx - minx) if maxx > minx else 1.0
    fig = plt.figure(figsize=(width_in, max(2.0, width_in * aspect)))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0.03, 0.03, 0.94, 0.94])
    ax.imshow(img, extent=(0, w_mm, 0, h_mm), origin="upper", interpolation="antialiased", zorder=1)
    _overlay(ax, layout, minx, maxx, miny, maxy, caption, show_anchors)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out), dpi=fig.dpi, facecolor="white")
    plt.close(fig)
    record = file_record(out)
    record.update({"dpi": int(fig.dpi), "shown_extents": [minx, miny, maxx, maxy],
                   "source_raster_dpi": dpi, "balloons_drawn": len(layout.balloons),
                   "balloons_not_drawn": list(layout.unplaced), "entities_not_drawn": [],
                   "ink_fraction": ink_fraction(out)})
    return record


def ink_fraction(png_path: str | Path) -> float:
    """Share of pixels that are not white. A render can be technically successful and still show
    nothing, and a blank page in an inspection report is worse than an error."""
    import numpy as np
    from PIL import Image

    a = np.asarray(Image.open(png_path).convert("L"), dtype=float)
    if a.size == 0:
        return 0.0
    return float((a < 250).mean())


def render_sheet(src_path: str | Path, route: str, frame, out_path: str | Path,
                 *, page: int = 1, max_px: int = 3200) -> dict:
    """One page of the sheet alone - no balloons, no caption, no margin.

    ``frame`` is (minx, miny, maxx, maxy) in drawing units and becomes exactly the image's edges,
    so a drawing coordinate maps to a pixel by a plain linear scale. It is rendered BEFORE the
    balloons are placed: its ink is what the layout keeps clear of, and the interactive viewer and
    the layout PNG draw balloons over this same picture. A scan is deskewed exactly as the reader
    deskewed it, so positions read from the page land on the page."""
    if hasattr(frame, "page"):           # a Layout (callers from before the frame argument)
        minx, maxx, miny, maxy = _ax_limits(frame.page, frame.slot_pitch / 2)
    else:
        minx, miny, maxx, maxy = (float(v) for v in frame)
    w, h = maxx - minx, maxy - miny
    if w <= 0 or h <= 0:
        raise ValueError("degenerate sheet extents")
    dpi = 100
    scale = max_px / max(w, h)
    fig = plt.figure(figsize=(w * scale / dpi, h * scale / dpi), dpi=dpi)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("white")
    not_drawn: list[str] = []
    src = Path(src_path)
    if route == "pdf-vector":
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(src))
        pg = doc[page - 1]
        w_pt, h_pt = pg.get_size()
        img = pg.render(scale=min(6.0, max(1.0, scale * 25.4 / 72.0))).to_pil().convert("RGB")
        ax.imshow(img, extent=(0, w_pt * 25.4 / 72, 0, h_pt * 25.4 / 72), origin="upper",
                  interpolation="antialiased")
    elif route == "raster-ocr":
        from ..ingest.raster import deskew, load_pages
        img, sdpi, _ = load_pages(src)[page - 1]
        img, _ = deskew(img)
        ax.imshow(img, extent=(0, img.width * 25.4 / sdpi, 0, img.height * 25.4 / sdpi),
                  origin="upper", interpolation="antialiased")
    else:
        if page != 1:
            raise ValueError("a DXF modelspace has one sheet")
        import ezdxf
        from ezdxf.addons.drawing import RenderContext
        doc = ezdxf.readfile(str(src))
        msp = doc.modelspace()
        backend, not_drawn = _draw_modelspace(msp, ax, RenderContext(doc))
        _draw_frames(msp, ax)
        backend.finalize()
        # ezdxf's finalize resizes the figure to the data's aspect; the viewer needs the sheet's
        fig.set_size_inches(w * scale / dpi, h * scale / dpi, forward=True)
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("auto")
    ax.set_axis_off()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out), dpi=dpi, facecolor="white")
    plt.close(fig)
    from PIL import Image
    with Image.open(out) as im:
        px = im.size
    record = file_record(out)
    record.update({"page": page, "extents": [minx, miny, maxx, maxy], "width_px": px[0],
                   "height_px": px[1], "entities_not_drawn": not_drawn,
                   "ink_fraction": ink_fraction(out)})
    return record


# Status colours shared with the workspace (docs/design-system/MASTER.md); print-safe on white.
TONE = {"info": "#2456e6", "warn": "#b86400", "bad": "#c62038", "ok": "#0f7a4f", "na": "#6b7480"}
TONE_LABEL = {"info": "Detected", "warn": "Needs review", "bad": "Blocking", "ok": "Approved",
              "na": "Rejected"}


def row_tone(row: dict) -> str:
    """The same status words and colours the workspace shows: never colour alone."""
    if row.get("status") == "approved":
        return "ok"
    if row.get("status") == "rejected":
        return "na"
    if row.get("blocking"):
        return "bad"
    if row.get("status") == "needs_review":
        return "warn"
    return "info"


def render_layout(sheet_png: str | Path, extents, balloons, tones: dict[str, str],
                  out_path: str | Path, *, title: str = "", subtitle: str = "",
                  page: int = 1, pages: int = 1) -> dict:
    """The inspection layout: the sheet, with balloons drawn over it, under a title band.

    Balloons: white disc, status-coloured ring and bold number, a fine leader ending in a dot on
    the callout. The band names the drawing, revision and page and carries the status legend with
    counts (words, not colour alone), so a printed page explains itself."""
    from matplotlib.patches import Circle as _C
    from matplotlib.patches import Rectangle as _R
    from PIL import Image
    minx, miny, maxx, maxy = (float(v) for v in extents)
    img = Image.open(sheet_png).convert("RGB")
    W, H = img.size
    band = max(64, int(W * 0.03))
    dpi = 100
    fig = plt.figure(figsize=(W / dpi, (H + band) / dpi), dpi=dpi)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, H / (H + band)])
    ax.imshow(img, extent=(minx, maxx, miny, maxy), origin="upper", interpolation="antialiased")
    ax.set_xlim(minx, maxx)
    ax.set_ylim(miny, maxy)
    ax.set_aspect("auto")
    ax.set_axis_off()
    px = W / (maxx - minx)                        # pixels per drawing unit
    pt = 72.0 / dpi                               # points per pixel
    counts: dict[str, int] = {}
    for b in balloons:
        t = tones.get(b.cid, "info")
        counts[t] = counts.get(t, 0) + 1
        col = TONE[t]
        r_px = b.radius * px
        lw = max(1.4, r_px * 0.11) * pt
        (ax0, ay0), (ex, ey) = b.leader[0], b.leader[-1]
        ax.plot([ax0, ex], [ay0, ey], color=col, linewidth=max(0.9, lw * 0.5),
                solid_capstyle="round", zorder=5)
        ax.add_patch(_C((ax0, ay0), b.radius * 0.14, facecolor=col, edgecolor="white",
                        linewidth=lw * 0.3, zorder=6))
        ax.add_patch(_C(b.centre, b.radius, facecolor="white", edgecolor=col, linewidth=lw,
                        zorder=7))
        label = str(b.number)
        fs = r_px * (1.0 if len(label) < 2 else 0.86 if len(label) < 3 else 0.66) * pt
        ax.text(b.centre[0], b.centre[1], label, ha="center", va="center_baseline",
                fontsize=fs, fontweight="bold", color=col, zorder=8, family="DejaVu Sans")
    # title band: name and context left, legend right
    hb = fig.add_axes([0, H / (H + band), 1, band / (H + band)])
    hb.set_xlim(0, W)
    hb.set_ylim(0, band)
    hb.set_axis_off()
    hb.add_patch(_R((0, 0), W, band, facecolor="#0e1420", edgecolor="none"))
    hb.add_patch(_R((0, 0), band * 0.08, band, facecolor="#2456e6", edgecolor="none"))
    big, small = band * 0.30 * pt, band * 0.20 * pt
    hb.text(band * 0.45, band * 0.64, title, color="white", fontsize=big, fontweight="bold",
            va="center", family="DejaVu Sans")
    hb.text(band * 0.45, band * 0.27,
            subtitle + (f"   |   page {page} of {pages}" if pages > 1 else ""),
            color="#aab4c3", fontsize=small, va="center", family="DejaVu Sans")
    x = W - band * 0.45
    for t in ("na", "ok", "bad", "warn", "info"):
        if not counts.get(t):
            continue
        label = f"{TONE_LABEL[t]}  {counts[t]}"
        tw = len(label) * band * 0.118 + band * 0.62
        x -= tw
        hb.add_patch(_C((x + band * 0.18, band * 0.5), band * 0.14, facecolor="white",
                        edgecolor=TONE[t], linewidth=band * 0.05 * pt))
        hb.text(x + band * 0.42, band * 0.5, label, color="white", fontsize=small,
                va="center", family="DejaVu Sans")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out), dpi=dpi, facecolor="white")
    plt.close(fig)
    record = file_record(out)
    record.update({"dpi": dpi, "shown_extents": [minx, miny, maxx, maxy], "page": page,
                   "balloons_drawn": len(balloons), "balloons_not_drawn": [],
                   "entities_not_drawn": [], "ink_fraction": ink_fraction(out)})
    return record
