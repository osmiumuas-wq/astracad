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


def render_sheet(src_path: str | Path, route: str, layout: Layout, out_path: str | Path,
                 *, max_px: int = 2400) -> dict:
    """The sheet alone - no balloons, no caption, no margin - for the interactive viewer.

    The axes fill the whole image and its limits are exactly ``extents``, so a drawing coordinate
    maps to a pixel by a plain linear scale. The viewer draws balloons, leaders and anchors itself
    from ``plan.layout``: the same numbers the PNG/PDF overlays use, so the two cannot disagree."""
    minx, maxx, miny, maxy = _ax_limits(layout.page, layout.slot_pitch / 2)
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
        page = pdfium.PdfDocument(str(src))[0]
        w_pt, h_pt = page.get_size()
        img = page.render(scale=min(6.0, max(1.0, scale * 25.4 / 72.0))).to_pil().convert("RGB")
        ax.imshow(img, extent=(0, w_pt * 25.4 / 72, 0, h_pt * 25.4 / 72), origin="upper",
                  interpolation="antialiased")
    elif route == "raster-ocr":
        from ..ingest.raster import load_pages
        img, sdpi, _ = load_pages(src)[0]
        ax.imshow(img, extent=(0, img.width * 25.4 / sdpi, 0, img.height * 25.4 / sdpi),
                  origin="upper", interpolation="antialiased")
    else:
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
    record.update({"extents": [minx, miny, maxx, maxy], "width_px": px[0], "height_px": px[1],
                   "entities_not_drawn": not_drawn, "ink_fraction": ink_fraction(out)})
    return record
