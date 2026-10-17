"""Raster / scanned drawing route: two independent OCR engines, accepted only where they agree.

A scan has no text layer and no geometry, so every number on it comes from OCR, and OCR fails in
confident ways (measured on this PC: Windows OCR read "45°" as "450"; RapidOCR reads "Ø20" as
"020"). One engine's output is therefore never accepted on its own:

* **Windows.Media.Ocr** (built into Windows 10/11, word boxes) and **RapidOCR** (PaddleOCR models
  on onnxruntime, line boxes + confidence) read the same page independently;
* a region becomes a characteristic only when both engines read the same text there. The one
  tolerated difference is the diameter sign read as "0"/"O" by one engine where the other read
  "Ø" — recorded on the row;
* a disagreement, or text seen by only one engine, is escalated with both readings;
* every accepted row still needs review: there are no definition points to cross-check against.

The page is read upright and turned 90° clockwise (vertical dimension text reads bottom-to-top),
and boxes are mapped back to the upright page. Positions are page millimetres (from the image
DPI; 300 assumed and stated when the file carries none). Units are never assumed: --units is
required, exactly as for PDF.
"""
from __future__ import annotations

import asyncio
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from .. import iso286
from ..callouts import normalise, parse_callout
from ..gdt import parse_unicode_frame
from ..model import (CharKind, Characteristic, Check, CheckOutcome, ProvenanceState,
                     ReviewStatus, SourceLocation, UninterpretedAnnotation)
from ..units import UnitError
from .dxf import DrawingBundle, EntityCensusRow, sha256_file

RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
DEFAULT_DPI = 300.0
_NUMBER = re.compile(r"\d")
_CALLOUT_START = re.compile(r"^(?:\d+\s*x\s*)?(?:⌀|R\s*\d|M\d|\d|\.\d)", re.I)
_DEVIATION_ONLY = re.compile(r"^[+-]\s*\d*\.?\d+$")


@dataclass
class OcrBox:
    text: str
    box: tuple[float, float, float, float]          # x0, y0, x1, y1 in upright image pixels
    conf: float | None = None
    tilt: float = 0.0          # text baseline angle in the read image (deg, y down), RapidOCR only


@dataclass
class Region:
    box: tuple[float, float, float, float]
    a: str | None                                   # Windows reading
    b: str | None                                   # RapidOCR reading
    angle: int
    b_conf: float | None = None
    agreed: str | None = None
    notes: list[str] = field(default_factory=list)
    skip: str | None = None          # border label / balloon / table cell (from sheet geometry)
    tilt: float = 0.0                # text angle within the pass (RapidOCR), used to level re-reads


# ---- engines --------------------------------------------------------------------------------
class WindowsOcr:
    name = "windows-media-ocr"

    def __init__(self, language: str = "en-US"):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        self._engine = OcrEngine.try_create_from_language(Language(language))
        if self._engine is None:
            raise RuntimeError(f"Windows OCR has no recogniser for {language}")
        self.max_dim = OcrEngine.max_image_dimension

    def words(self, img) -> list[OcrBox]:
        return asyncio.run(self._words(img))

    async def _words(self, img) -> list[OcrBox]:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
        scale = 1.0
        if max(img.size) > self.max_dim:
            scale = self.max_dim / max(img.size)
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        stream = InMemoryRandomAccessStream()
        w = DataWriter(stream)
        w.write_bytes(buf.getvalue())
        await w.store_async()
        await w.flush_async()
        stream.seek(0)
        bmp = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
        res = await self._engine.recognize_async(bmp)
        out = []
        for line in res.lines:
            for wd in line.words:
                r = wd.bounding_rect
                out.append(OcrBox(wd.text, (r.x / scale, r.y / scale, (r.x + r.width) / scale,
                                            (r.y + r.height) / scale)))
        return out


class RapidOcrEngine:
    name = "rapidocr-onnxruntime"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        self._engine = RapidOCR()

    def lines(self, img) -> list[OcrBox]:
        import numpy as np
        res, _ = self._engine(np.asarray(img))
        out = []
        import math
        for pts, text, conf in res or []:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            tilt = math.degrees(math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0]))
            out.append(OcrBox(text, (min(xs), min(ys), max(xs), max(ys)), float(conf), tilt))
        return out


def engines_available() -> dict[str, str]:
    """Which engines import here, and why not when they do not."""
    got = {}
    for cls in (RapidOcrEngine, WindowsOcr):        # RapidOCR first - see read_raster
        try:
            cls()
            got[cls.name] = "available"
        except Exception as ex:          # noqa: BLE001 - report, do not fail
            got[cls.name] = f"unavailable: {type(ex).__name__}: {ex}"
    return got


# ---- geometry helpers ------------------------------------------------------------------------
def _unrotate(box, angle: int, w: int, h: int):
    """Map a box from an image rotated by `angle` (PIL, CCW, expand=True) back to upright pixels."""
    x0, y0, x1, y1 = box
    if angle == 0:
        return box
    if angle == -90:          # rotated clockwise: upright (x, y) -> (h - y, x)
        pts = [(yy, h - xx) for xx, yy in ((x0, y0), (x1, y1))]
    elif angle == 90:         # rotated counter-clockwise: upright (x, y) -> (y, w - x)
        pts = [(w - yy, xx) for xx, yy in ((x0, y0), (x1, y1))]
    else:
        raise ValueError(angle)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _overlap(a, b) -> float:
    """Intersection over the smaller box (containment counts as full overlap)."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1.0
    return inter / small


# ---- agreement -------------------------------------------------------------------------------
_DIA = "⌀Øø∅Φφ"          # RapidOCR writes the diameter sign as Greek phi too


def _key(t: str) -> str:
    import unicodedata
    t = normalise(unicodedata.normalize("NFKC", t or ""))   # full-width digits (RapidOCR '７')
    return re.sub(r"\s+", "", t)


def agree(a: str | None, b: str | None) -> tuple[str | None, list[str]]:
    """The agreed text, or None. Only the diameter-sign confusion is tolerated, and only when one
    engine actually read a diameter sign at that place."""
    if not a or not b:
        return None, []
    ka, kb = _key(a), _key(b)
    if ka == kb:
        return normalise(a), []
    if len(ka) == len(kb):
        merged, notes = [], []
        for ca, cb in zip(ka, kb):
            if ca == cb:
                merged.append(ca)
            elif ca in _DIA and cb in "0Oo@":           # RapidOCR also reads ⌀ as '@'
                merged.append("⌀")
                notes.append(f"RapidOCR read the diameter sign as {cb!r}; Windows OCR read ⌀")
            elif cb in _DIA and ca in "0Oo":
                merged.append("⌀")
                notes.append(f"Windows OCR read the diameter sign as {ca!r}; RapidOCR read ⌀")
            elif {ca, cb} & {"°"} and ({ca, cb} - {"°"}) <= set("0Oo"):
                # Measured: Windows OCR reads "45°" as "450" at every scale. Accepted only when the
                # other engine explicitly read the degree sign at that position.
                merged.append("°")
                other = cb if ca == "°" else ca
                notes.append(f"one engine read the degree sign as {other!r}; the other read °")
            elif {ca, cb} == {"0", "*"} and len(merged) == len(ka) - 1 and merged and merged[-1].isdigit():
                # last character only: Windows writes the degree sign as '0' and RapidOCR as '*'
                # (measured: '450'/'45*', '880'/'88*'); neither is a digit position in a value
                merged.append("°")
                notes.append("degree sign read as '0' by one engine and '*' by the other")
            elif {ca, cb} == {"1", "."} and "." not in (ka if ca == "1" else kb):
                # Measured on an AutoCAD stroke font: Windows OCR reads every decimal point as '1'
                # ('11345' for '1.345'). Accepted only where the other engine read '.' at that
                # position and the '1'-reading has no decimal point anywhere (the failure's signature).
                merged.append(".")
                who = "Windows OCR" if ca == "1" else "RapidOCR"
                notes.append(f"{who} read the decimal point as '1' (it shows no decimal point at all); "
                             "the other engine read '.' there")
            else:
                return None, []
        return "".join(merged), notes
    # one engine dropped the decimal point entirely ('27940' vs '27.940'): accepted only when the
    # other's reading minus its single '.' is identical and the dropping engine shows no '.' at all
    for x, y, who in ((ka, kb, "Windows OCR"), (kb, ka, "RapidOCR")):
        if "." not in x and y.count(".") == 1 and y.replace(".", "") == x and re.search(r"\d\.\d", y):
            return normalise(b if y == kb else a), [f"{who} dropped the decimal point; the other "
                                                    "engine read it"]
    return None, []


def _join(words: list[OcrBox]) -> str:
    words = sorted(words, key=lambda w: (round((w.box[1] + w.box[3]) / 2 / 8), w.box[0]))
    return " ".join(w.text for w in words)


def reread(img, region: Region, win: WindowsOcr, rapid: RapidOcrEngine,
           scales=(2, 3)) -> None:
    """Second look at a region the engines did not agree on: crop it (with margin), turn it the
    way it was read, upscale, and let BOTH engines read it again. Agreement is still required;
    the re-read only gives each engine more pixels."""
    from PIL import Image
    x0, y0, x1, y1 = region.box
    h = max(8.0, y1 - y0) if region.angle == 0 else max(8.0, x1 - x0)
    pad = 0.6 * h
    crop = img.crop((max(0, int(x0 - pad)), max(0, int(y0 - pad)),
                     min(img.width, int(x1 + pad)), min(img.height, int(y1 + pad))))
    if region.angle:
        crop = crop.rotate(region.angle, expand=True)
    if 3.0 <= abs(region.tilt) <= 87.0:
        # text on an angled leader: Windows OCR has no orientation detection and missed every
        # such callout on the SOLIDWORKS samples (R13, R44, 2XR7 ...). Level the crop for BOTH engines.
        crop = crop.rotate(region.tilt, expand=True, fillcolor=(255, 255, 255))
    for k in scales:
        big = crop.resize((crop.width * k, crop.height * k), Image.LANCZOS)
        a = _join(win.words(big)) or None
        bl = rapid.lines(big)
        b = " ".join(x.text for x in sorted(bl, key=lambda x: (x.box[1], x.box[0]))) or None
        text, notes = agree(a, b)
        if text is not None:
            region.a, region.b, region.agreed = a, b, text
            region.notes = notes + [f"agreed on a {k}x re-read of the region (first read: "
                                    f"Windows {region.a!r}, RapidOCR {region.b!r})"]
            return
    region.notes.append(f"no agreement after re-reading the region at {', '.join(f'{k}x' for k in scales)}")


def regions_for(img, win: WindowsOcr, rapid: RapidOcrEngine, angles=(0, -90), geo=None,
                dpi: float = DEFAULT_DPI) -> list[Region]:
    """Read the page at each angle with both engines and pair their readings by position.
    With sheet geometry, regions that are border labels, balloons or table cells are marked
    (`skip`) before any re-read is spent on them."""
    W, H = img.size
    mm = 25.4 / dpi
    regions: list[Region] = []
    budget = REREAD_BUDGET
    for angle in angles:
        rot = img if angle == 0 else img.rotate(angle, expand=True)
        ww = [OcrBox(w.text, _unrotate(w.box, angle, W, H)) for w in win.words(rot)]
        rr = [OcrBox(r.text, _unrotate(r.box, angle, W, H), r.conf, r.tilt) for r in rapid.lines(rot)]
        used = set()
        found: list[Region] = []
        for r in rr:
            mine = [i for i, w in enumerate(ww) if i not in used and _overlap(w.box, r.box) > 0.5]
            used.update(mine)
            a = _join([ww[i] for i in mine]) if mine else None
            box = r.box
            for i in mine:
                b = ww[i].box
                box = (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
            found.append(Region(box, a, r.text, angle, r.conf, tilt=r.tilt))
        # Windows words no RapidOCR line covered: one-engine readings, grouped by line proximity
        rest = [ww[i] for i in range(len(ww)) if i not in used]
        while rest:
            seed = rest.pop(0)
            group = [seed]
            for w in rest[:]:
                if abs((w.box[1] + w.box[3]) - (seed.box[1] + seed.box[3])) / 2 < 0.6 * (seed.box[3] - seed.box[1]) \
                        and min(abs(w.box[0] - g.box[2]) for g in group) < 1.5 * (seed.box[3] - seed.box[1]):
                    group.append(w)
                    rest.remove(w)
            box = (min(g.box[0] for g in group), min(g.box[1] for g in group),
                   max(g.box[2] for g in group), max(g.box[3] for g in group))
            found.append(Region(box, _join(group), None, angle))
        if geo is not None:
            geo.centres = [((r.box[0] + r.box[2]) / 2 * mm, (H - (r.box[1] + r.box[3]) / 2) * mm)
                           for r in regions + found]
        for f in found:
            f.agreed, f.notes = agree(f.a, f.b)
            f.skip = _classify_region(geo, f.box, f.agreed or f.a or f.b or "", H, mm)
            if f.skip is None and f.agreed is None and _NUMBER.search((f.a or "") + (f.b or "")) \
                    and budget <= 0:
                f.notes.append(f"re-read budget of {REREAD_BUDGET} per page exhausted")
            if f.skip is None and budget > 0 and f.agreed is None and _NUMBER.search((f.a or "") + (f.b or "")) and \
                    not any(_overlap(f.box, g.box) > 0.5 for g in regions):
                budget -= 1
                first = (f.a, f.b)
                reread(img, f, win, rapid)
                if f.agreed is not None:
                    f.notes[-1] = (f"agreed on a re-read of the region (first read: Windows "
                                   f"{first[0]!r}, RapidOCR {first[1]!r})")
            # a later (rotated) pass re-reads text already read upright as noise: keep the first
            if any(_overlap(f.box, g.box) > 0.5 for g in regions):
                continue
            regions.append(f)
    return regions


# ---- deskew -----------------------------------------------------------------------------------
def estimate_skew(img) -> float | None:
    """Page rotation in degrees (PIL convention: positive = content turned counter-clockwise),
    from long straight lines near horizontal/vertical, length-weighted median. None if too few."""
    import cv2
    import numpy as np
    g = np.asarray(img.convert("L"))
    small = 1600 / max(g.shape)                        # speed: estimate on a reduced copy
    if small < 1:
        g = cv2.resize(g, (int(g.shape[1] * small), int(g.shape[0] * small)), interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(cv2.GaussianBlur(g, (3, 3), 0), 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 1800, threshold=80,
                            minLineLength=int(0.08 * min(g.shape)), maxLineGap=4)
    if lines is None:
        return None
    ang, w = [], []
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):      # OpenCV 4 (N,1,4) or 5 (N,4)
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        a = (a + 45) % 90 - 45                         # fold verticals onto horizontals
        if abs(a) <= 5:
            ang.append(a)
            w.append(np.hypot(x2 - x1, y2 - y1))
    if len(ang) < 5:
        return None
    order = np.argsort(ang)
    a, w = np.asarray(ang)[order], np.asarray(w)[order]
    return float(a[np.searchsorted(np.cumsum(w), w.sum() / 2)])


def deskew(img):
    """Rotate the page upright when it is measurably skewed; returns (img, correction in degrees).

    estimate_skew measures line angles in image coordinates (y down): content turned by +r (PIL,
    counter-clockwise) shows lines at -r, and rotate(-r) undoes it - so the correction IS the
    measured angle (verified on pages degraded by known angles). A second pass on the corrected
    page refines the first (measured first-pass bias ~0.1 deg)."""
    from PIL import Image

    def turn(angle):
        return img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))
    a1 = estimate_skew(img)
    if a1 is None or abs(a1) < 0.05:
        return img, 0.0
    corr = _refine_by_profile(img, a1)
    return turn(corr), corr


def _refine_by_profile(img, coarse: float, span: float = 0.4, step: float = 0.02) -> float:
    """Pick the rotation near `coarse` whose row and column ink profiles are sharpest: straight
    rules line up with pixel rows/columns only when the page is truly upright. Hough endpoints
    alone left ~0.1 deg (measured); this resolves to the step size."""
    import cv2
    import numpy as np
    g = np.asarray(img.convert("L"))
    k = 2000 / max(g.shape)
    if k < 1:
        g = cv2.resize(g, (int(g.shape[1] * k), int(g.shape[0] * k)), interpolation=cv2.INTER_AREA)
    ink = (255 - g).astype(np.float32)
    h, w = ink.shape
    best, best_score = coarse, -1.0
    for a in np.arange(coarse - span, coarse + span + 1e-9, step):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)       # same convention as PIL rotate
        r = cv2.warpAffine(ink, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)
        score = float(np.var(r.sum(axis=1)) + np.var(r.sum(axis=0)))
        if score > best_score:
            best, best_score = float(a), score
    return best


# ---- sheet geometry from pixels -------------------------------------------------------------
REREAD_BUDGET = 40          # per page: a cluttered sheet took 572 s without a cap (measured)


def raster_geometry(img, dpi: float):
    """Rules and circles recovered from the image, in page mm (y up), wrapped in the same
    SheetGeometry the vector-PDF route uses: so border labels, balloons and table cells are told
    apart from callouts the same way on a scan as on a vector sheet."""
    import cv2
    import numpy as np

    from .pdf import SheetGeometry
    g = np.asarray(img.convert("L"))
    H, W = g.shape
    mm = 25.4 / dpi
    bw = (g < 160).astype(np.uint8) * 255
    L = max(5, int(3.0 / mm))                   # rules at least 3 mm long
    segs = []
    for kernel, horiz in (((L, 1), True), ((1, L), False)):
        lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
        n, _, stats, _ = cv2.connectedComponentsWithStats(lines)
        for x, y, w, h, area in stats[1:]:
            if horiz:
                yc = (H - (y + h / 2)) * mm
                segs.append((x * mm, yc, (x + w) * mm, yc))
            else:
                xc = (x + w / 2) * mm
                segs.append((xc, (H - y - h) * mm, xc, (H - y) * mm))
    circles = cv2.HoughCircles(cv2.GaussianBlur(g, (5, 5), 1.2), cv2.HOUGH_GRADIENT, dp=1.5,
                               minDist=int(4 / mm), param1=120, param2=40,
                               minRadius=int(2.0 / mm), maxRadius=int(10.0 / mm))
    if circles is not None:
        for cx, cy, r in circles[0]:
            pts = [((cx + r * np.cos(t)) * mm, (H - (cy + r * np.sin(t))) * mm)
                   for t in np.linspace(0, 2 * np.pi, 25)]
            segs += [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])]
    return SheetGeometry(segs, W * mm, H * mm)


def _classify_region(geo, box_px, text: str, img_h: int, mm: float) -> str | None:
    if geo is None or not text:
        return None
    from ..callouts import normalise
    t = normalise(text).strip()
    x0, y0, x1, y1 = box_px
    box = (x0 * mm, (img_h - y1) * mm, x1 * mm, (img_h - y0) * mm)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    th = max(box[3] - box[1], 1.0)
    if geo.is_border_label(t, cx, cy):
        return "border zone label"
    if geo.is_balloon(t, cx, cy, th):
        return "balloon item number"
    if geo.is_table_cell(box, th):
        return "table cell"
    return None


_DEV = re.compile(r"^[+\-±]\s*\d*\.?\d+$")


def _attach_stacked(chars, regions, unit: str) -> None:
    """A tolerance stacked right of a nominal is a separate OCR region. Both deviations agreed ->
    joined into the row; anything tolerance-like beside the nominal that is not agreed -> the row is
    blocked, because a row reading 'no tolerance' where the drawing states one is a silent error."""
    from ..limits import resolve_limits
    from ..model import Deviations, NoTolerance
    for c in chars:
        box = getattr(c, "_box_px", None)
        if box is None or not isinstance(c.tolerance, NoTolerance) or c.nominal is None:
            continue
        x0, y0, x1, y1 = box
        h = max(y1 - y0, 1.0)
        mid = (y0 + y1) / 2
        beside = [r for r in regions
                  if x1 - 0.3 * h <= r.box[0] <= x1 + 1.5 * h
                  and y0 - 1.2 * h <= (r.box[1] + r.box[3]) / 2 <= y1 + 1.2 * h
                  and r.box is not box]
        devs = [r for r in beside if _DEV.match(_key(r.agreed or r.a or r.b or ""))]
        if not devs:
            continue
        agreed = [r for r in devs if r.agreed and _DEV.match(_key(r.agreed))]
        upper = [r for r in agreed if (r.box[1] + r.box[3]) / 2 < mid]    # image y grows down
        lower = [r for r in agreed if (r.box[1] + r.box[3]) / 2 >= mid]
        if len(devs) == 2 and len(upper) == 1 and len(lower) == 1:
            up = float(_key(upper[0].agreed).replace("±", ""))
            lo = float(_key(lower[0].agreed).replace("±", ""))
            if up >= lo:
                c.tolerance = Deviations(upper=up, lower=lo)
                c.limits = resolve_limits(c.nominal, c.tolerance, unit)
                c.raw_callout = f"{c.raw_callout} {upper[0].agreed.strip()}/{lower[0].agreed.strip()}"
                c.issues = [i for i in c.issues if i.code != "no_limits"]
                c.record("ocr", "joined", f"stacked tolerance {up:+g}/{lo:+g} from two agreed regions")
                continue
        readings = "; ".join(f"Windows {r.a!r} / RapidOCR {r.b!r}" for r in devs)
        c.fail("ocr_adjacent_tolerance_unread",
               f"tolerance-like text beside this value was not read by both engines ({readings}); "
               "the row must not be taken as untoleranced", "blocking")


# ---- page loading ----------------------------------------------------------------------------
def load_pages(path: Path) -> list[tuple[object, float, bool]]:
    """(RGB image, dpi, dpi_was_stated) per page."""
    from PIL import Image, ImageSequence
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        return [(pdf[i].render(scale=DEFAULT_DPI / 72.0).to_pil().convert("RGB"), DEFAULT_DPI, True)
                for i in range(len(pdf))]
    im = Image.open(path)
    pages = []
    for frame in ImageSequence.Iterator(im):
        dpi = frame.info.get("dpi")
        stated = bool(dpi and dpi[0] and dpi[0] > 1)
        pages.append((frame.convert("RGB"), float(dpi[0]) if stated else DEFAULT_DPI, stated))
    return pages


# ---- bundle ----------------------------------------------------------------------------------
def read_raster(path: str | Path, declared_unit: str | None = None,
                revision: str | None = None, *, engines=None,
                angles=(0, -90)) -> DrawingBundle:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"drawing not found: {p}")
    if not declared_unit:
        raise UnitError("a scanned drawing does not declare the unit its callouts are written in. "
                        "Pass --units explicitly; nothing is assumed.")
    if engines is None:
        # Order matters: creating RapidOCR (onnxruntime) after WinRT has been initialised in the
        # process segfaults (measured on this PC); created first, both coexist.
        rapid = RapidOcrEngine()
        win = WindowsOcr()
    else:
        win, rapid = engines
    doc_sha = sha256_file(p)
    unit = declared_unit
    warnings: list[str] = []
    if revision:
        revision, revision_source = revision.strip() or "A", "user-specified"
    else:
        revision, revision_source = "A", ("default - title blocks are not read from scans by "
                                          "this route; confirm before issuing")
        warnings.append("revision defaults to A: this route does not read title blocks")

    pages = load_pages(p)
    if len(pages) > 1:
        warnings.append(f"{len(pages)} pages: every page is read; layout image shows page 1")
    characteristics, uninterpreted, notes = [], [], []
    from collections import Counter
    skipped: Counter = Counter()
    zone_labels: list = []
    n_regions = n_agreed = n_disagree = n_single = 0
    page_w = page_h = 0.0
    for pno, (img, dpi, stated) in enumerate(pages, start=1):
        if not stated:
            warnings.append(f"page {pno}: the image states no DPI; {DEFAULT_DPI:g} assumed for "
                            "positions only (values are unaffected)")
        mm = 25.4 / dpi
        if pno == 1:
            page_w, page_h = img.width * mm, img.height * mm
        img, skew = deskew(img)
        if skew:
            warnings.append(f"page {pno}: rotated {skew:+.2f} deg to deskew (estimated from long straight "
                            "lines); positions refer to the deskewed page")
        try:
            geo = raster_geometry(img, dpi)
        except Exception as ex:                 # geometry is an aid, not a requirement
            geo = None
            warnings.append(f"page {pno}: sheet geometry not recovered ({type(ex).__name__}: {ex})")
        page_regions = regions_for(img, win, rapid, angles, geo, dpi)
        first_char = len(characteristics)
        for i, r in enumerate(page_regions):
            n_regions += 1
            if r.skip:
                skipped[r.skip] += 1
                if r.skip == "border zone label":
                    zone_labels.append((pno, normalise(r.agreed or r.a or r.b or ""),
                                        (r.box[0] + r.box[2]) / 2 * mm,
                                        (img.height - (r.box[1] + r.box[3]) / 2) * mm))
                continue
            cx = (r.box[0] + r.box[2]) / 2 * mm
            cy = (img.height - (r.box[1] + r.box[3]) / 2) * mm
            handle = f"P{pno}-R{i:03d}@{r.angle}"
            readings = f"Windows OCR: {r.a!r}; RapidOCR: {r.b!r}"
            text = r.agreed
            if text is None:
                seen = r.a or r.b or ""
                if not _NUMBER.search(seen) or not (_CALLOUT_START.match(normalise(seen))
                                                    or normalise(seen)[:1] in "+-±"):
                    if _NUMBER.search(seen):
                        notes.append(normalise(seen))
                    continue
                if not (r.a and r.b) and re.fullmatch(r"[1Il|:\s]+", seen):
                    # section hatching read by one engine as runs of '1' (measured: ~60 on a
                    # mould base); not text, so it is counted rather than sent to review
                    skipped["hatching or line noise"] += 1
                    continue
                if r.a and r.b:
                    n_disagree += 1
                    why = "the two OCR engines disagree - " + readings
                else:
                    n_single += 1
                    why = "read by one OCR engine only - " + readings
                uninterpreted.append(UninterpretedAnnotation(
                    str(p), handle, "", seen, why, pno, cx, cy))
                continue
            n_agreed += 1
            if not _NUMBER.search(text):
                continue
            frame = parse_unicode_frame(text)
            parsed = None if frame is not None else parse_callout(text, unit)
            if frame is not None and frame.resolved:
                tol, kind, nominal, limits, count = frame.tolerance, CharKind.GEOMETRIC, None, \
                    (0.0, frame.tolerance.zone), 1
            elif parsed is not None and parsed.resolved and (
                    parsed.nominal is not None or parsed.tolerance.kind == "limit_pair") \
                    and not _DEVIATION_ONLY.match(_key(text)):
                from ..limits import resolve_limits
                tol, nominal, count = parsed.tolerance, parsed.nominal, parsed.count
                kind = parsed.kind if parsed.kind is not CharKind.UNKNOWN else CharKind.LINEAR
                limits = resolve_limits(nominal, tol, unit)
            else:
                if _CALLOUT_START.match(normalise(text)) or normalise(text)[:1] in "+-±":
                    why = (frame.unresolved_reason if frame is not None else
                           (parsed.unresolved_reason or "not a recognised callout"))
                    uninterpreted.append(UninterpretedAnnotation(
                        str(p), handle, "", text, f"both engines read {text!r}, but it does not "
                        f"parse as a callout: {why}", pno, cx, cy))
                elif _NUMBER.search(text):
                    notes.append(text)
                continue
            c = Characteristic(
                cid=f"CH-{len(characteristics) + 1:03d}", kind=kind, raw_callout=text, unit=unit,
                nominal=nominal, tolerance=tol, count=count, state=ProvenanceState.PARSED,
                review=ReviewStatus.NEEDS_REVIEW,
                source=SourceLocation(document=str(p), document_sha256=doc_sha, revision=revision,
                                      page=pno, entity_handle=handle, entity_type="OCR_REGION",
                                      layer="", anchor_x=cx, anchor_y=cy),
                limits=limits)
            c.record("extractor", "extracted", f"OCR consensus at {handle}: {readings}"
                     + (f", RapidOCR confidence {r.b_conf:.3f}" if r.b_conf is not None else ""))
            for note in r.notes:
                c.record("ocr", "noted", note)
            c.checks.append(Check("ocr_two_engine_agreement", CheckOutcome.PASSED,
                                  "two independent OCR engines read the same text", text))
            c.checks.append(Check("geometry_cross_check", CheckOutcome.NOT_VERIFIABLE,
                                  "callout matches drawn geometry", "-",
                                  "a scan has no definition points; the value is OCR-read only"))
            c.fail("ocr_read", "read from a raster by OCR (two engines agree); confirm against "
                               "the drawing before release", "warning")
            if c.limits is None and kind is not CharKind.ANGULAR:
                c.fail("no_limits", "no definite upper/lower limits could be derived from the "
                                    "stated tolerance", "warning")
            if kind is CharKind.GEOMETRIC and tol.modifier in ("MMC", "LMC"):
                c.fail("gdt_bonus_tolerance_not_applied",
                       f"{tol.symbol} at {tol.modifier}: bonus tolerance not computed", "warning")
            c._box_px = r.box
            characteristics.append(c)
        _attach_stacked(characteristics[first_char:], page_regions, unit)
        for c in characteristics[first_char:]:
            if hasattr(c, "_box_px"):
                delattr(c, "_box_px")

    from ..zones import assign_zones
    assign_zones(characteristics, zone_labels)
    extents = (0.0, 0.0, page_w, page_h)
    census = [EntityCensusRow("OCR_REGION", n_regions, len(characteristics),
                              f"{n_agreed} agreed, {n_disagree} disagreed, {n_single} one-engine")]
    for why, k in sorted(skipped.items()):
        census.append(EntityCensusRow(f"OCR_REGION:{why}", k, 0, f"{k} region(s) classified as {why} "
                                      "from the sheet's rules and circles; not treated as callouts"))
    feature_support = {
        "dimension.linear": "partial - OCR text with two-engine agreement; no geometry cross-check",
        "dimension.diameter": "partial - ⌀ from either engine when the other read 0/O at that place",
        "dimension.radius": "partial - R prefix in OCR text",
        "dimension.angular": "partial - ° must be read by both engines (\"450\" vs \"45°\" escalates)",
        "tolerance.symmetric_deviations": "inline only; stacked deviations are usually split by OCR "
                                          "and escalated",
        "tolerance.limit_pair": "supported when both engines agree",
        "tolerance.iso_fit_class": iso286.support_statement(),
        "gdt.feature_control_frame": "partial - only frames whose symbols OCR reads as text",
        "title_block": "NOT read by this route",
        "raster_ocr": "Windows.Media.Ocr + RapidOCR (PaddleOCR models), consensus required",
    }
    return DrawingBundle(
        path=str(p), sha256=doc_sha, revision=revision, revision_source=revision_source,
        unit=unit, extents=extents, content_extents=(page_w * 0.05, page_h * 0.05,
                                                     page_w * 0.95, page_h * 0.95),
        notes=notes, page_count=len(pages), characteristics=characteristics,
        uninterpreted=uninterpreted, census=census, warnings=warnings,
        feature_support=feature_support)
