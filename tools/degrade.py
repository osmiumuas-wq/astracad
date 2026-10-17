"""Turn clean drawing renders into scan-like images, deterministically.

SIMULATED DEGRADATION — not real scans. Used to measure how the OCR route holds up under the
defects a scanner adds: page rotation, grey paper, sensor noise, blur and JPEG compression. Every
parameter comes from a seed derived from the sheet name, so a run is reproducible and the exact
parameters are written next to each image.

    py tools/degrade.py [--corpus _sources/corpus_sw] [--out _sources/corpus_sw_scan]
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

DPI = 300


def params_for(name: str) -> dict:
    rng = np.random.default_rng(int(hashlib.sha256(name.encode()).hexdigest()[:8], 16))
    return {"rotate_deg": round(float(rng.uniform(0.5, 1.5) * rng.choice([-1, 1])), 3),
            "paper_grey": int(rng.integers(225, 242)), "noise_sigma": round(float(rng.uniform(6, 12)), 2),
            "blur_radius": round(float(rng.uniform(0.4, 0.9)), 2), "jpeg_quality": int(rng.integers(55, 75)),
            "seed": int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)}


def degrade(img: Image.Image, p: dict) -> Image.Image:
    g = img.convert("L").rotate(p["rotate_deg"], resample=Image.BICUBIC, expand=False, fillcolor=255)
    a = np.asarray(g, dtype=np.float32)
    a = a * (p["paper_grey"] / 255.0)                         # grey paper, ink stays dark
    rng = np.random.default_rng(p["seed"])
    a = np.clip(a + rng.normal(0, p["noise_sigma"], a.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(a).filter(ImageFilter.GaussianBlur(p["blur_radius"]))


def main() -> int:
    import pypdfium2 as pdfium
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    ap.add_argument("--corpus", default=str(root / "_sources" / "corpus_sw"))
    ap.add_argument("--out", default=str(root / "_sources" / "corpus_sw_scan"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for pdf in sorted(Path(a.corpus).glob("*_s*.pdf")):
        p = params_for(pdf.stem)
        img = pdfium.PdfDocument(str(pdf))[0].render(scale=DPI / 72.0).to_pil()
        dst = out / f"{pdf.stem}.jpg"
        degrade(img, p).save(dst, "JPEG", quality=p["jpeg_quality"], dpi=(DPI, DPI))
        (out / f"{pdf.stem}.degrade.json").write_text(json.dumps({**p, "source": pdf.name,
                                                                  "notice": "SIMULATED scan, not a real scan"}, indent=1))
        print(f"{dst.name}: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
