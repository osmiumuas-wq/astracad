"""Measure how readable a balloon layout is, on real drawings.

    py tools/benchmark_layout.py [--corpus _sources/corpus_sw] [--out _out/benchmark_layout.json]

Every sheet of the corpus (DXF and PDF) is run through the normal pipeline, then the layout is
scored from the files the run wrote - plan.json plus the balloon-free sheet image - so the score
is of what a user actually receives:

    crossings       pairs of leaders that cross each other
    covering_ink    balloons whose disc covers drawing ink (>3 % of the disc is dark pixels)
    leader_mean     mean leader length, in balloon diameters
    leader_p90      90th percentile leader length, in balloon diameters
    order_ratio     length of the path 1 -> 2 -> ... -> n through the balloon centres, divided by
                    the shortest such path's lower bound (the frame perimeter); lower = numbers run
                    in a readable sequence instead of jumping across the sheet
    unplaced        characteristics with no balloon
    overlaps        balloon pairs that intersect

Nothing here is a pass/fail judgement of correctness; it is the before/after yardstick for layout.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _cross(p1, p2, p3, p4) -> bool:
    def o(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)
    return (o(p1, p2, p3) * o(p1, p2, p4) < 0) and (o(p3, p4, p1) * o(p3, p4, p2) < 0)


def score(plan_path: Path) -> dict:
    import numpy as np
    from PIL import Image
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    stem = plan_path.name[:-len(".plan.json")]
    sheet = json.loads(plan_path.with_name(f"{stem}.sheet.json").read_text(encoding="utf-8"))
    pages = sheet.get("pages") or [{"page": 1, "image": sheet["image"], "extents": sheet["extents"]}]
    lay = plan["layout"]
    balloons = lay["balloons"]
    out = {"balloons": len(balloons), "unplaced": len(lay["unplaced"])}
    by_page: dict[int, list] = {}
    for b in balloons:
        by_page.setdefault(int(b.get("page", 1)), []).append(b)
    crossings = covering = overlaps = 0
    lens, path, perim = [], 0.0, 0.0
    for pg in pages:
        bs = by_page.get(int(pg["page"]), [])
        if not bs:
            continue
        img = np.asarray(Image.open(plan_path.with_name(pg["image"])).convert("L"))
        x0, y0, x1, y1 = pg["extents"]
        H, W = img.shape
        sx, sy = W / (x1 - x0), H / (y1 - y0)
        segs = [(tuple(b["leader"][0]), tuple(b["leader"][-1])) for b in bs if len(b["leader"]) >= 2]
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                if _cross(*segs[i], *segs[j]):
                    crossings += 1
        for i, b in enumerate(bs):
            r = b["radius"]
            lens.append(math.dist(b["leader"][0], b["leader"][-1]) / (2 * r) if len(b["leader"]) >= 2 else 0.0)
            cx, cy = (b["centre"][0] - x0) * sx, (y1 - b["centre"][1]) * sy
            rr = r * sx
            ys, xs = np.ogrid[max(0, int(cy - rr)):min(H, int(cy + rr) + 1), max(0, int(cx - rr)):min(W, int(cx + rr) + 1)]
            disc = (xs - cx) ** 2 + (ys - cy) ** 2 <= (rr * 0.92) ** 2
            patch = img[max(0, int(cy - rr)):min(H, int(cy + rr) + 1), max(0, int(cx - rr)):min(W, int(cx + rr) + 1)]
            if disc.any() and (patch[disc] < 160).mean() > 0.03:
                covering += 1
            for o in bs[i + 1:]:
                if math.dist(b["centre"], o["centre"]) < b["radius"] + o["radius"] - 1e-9:
                    overlaps += 1
        seq = sorted(bs, key=lambda b: b["number"])
        path += sum(math.dist(a["centre"], b["centre"]) for a, b in zip(seq, seq[1:]))
        perim += 2 * ((x1 - x0) + (y1 - y0))
    lens.sort()
    out.update(crossings=crossings, covering_ink=covering, overlaps=overlaps,
               leader_mean=round(sum(lens) / len(lens), 2) if lens else 0.0,
               leader_p90=round(lens[int(0.9 * (len(lens) - 1))], 2) if lens else 0.0,
               order_ratio=round(path / perim, 2) if perim else 0.0)
    return out


def run(corpus: Path, work: Path) -> dict:
    from astracad_inspect.pipeline import inspect_drawing
    rows = []
    for f in sorted(list(corpus.glob("*_s*.dxf")) + list(corpus.glob("*_s*.pdf"))):
        unit = None if f.suffix == ".dxf" else "mm"
        key = json.loads((corpus / (f.stem.rsplit("_s", 1)[0] + ".answer.json")).read_text(encoding="utf-8"))
        if f.suffix == ".pdf":
            unit = key.get("unit") or "mm"
        out = work / f"{f.stem}_{f.suffix[1:]}"
        try:
            inspect_drawing(f, out, declared_unit=unit)
            s = score(out / f"{f.stem}.plan.json")
        except Exception as e:  # noqa: BLE001 - a benchmark reports, it does not stop
            s = {"error": f"{type(e).__name__}: {e}"}
        rows.append({"sheet": f.name, **s})
        print(f"{f.name:<28} " + "  ".join(f"{k}={v}" for k, v in s.items()), flush=True)
    ok = [r for r in rows if "error" not in r and r["balloons"]]
    tot = lambda k: sum(r[k] for r in ok)
    summary = {"sheets": len(rows), "scored": len(ok), "balloons": tot("balloons"),
               "crossings": tot("crossings"), "covering_ink": tot("covering_ink"),
               "overlaps": tot("overlaps"), "unplaced": tot("unplaced"),
               "leader_mean": round(sum(r["leader_mean"] * r["balloons"] for r in ok) / max(1, tot("balloons")), 2),
               "order_ratio_mean": round(sum(r["order_ratio"] for r in ok) / max(1, len(ok)), 2)}
    print("SUMMARY", json.dumps(summary))
    return {"summary": summary, "sheets": rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default=str(ROOT / "_sources" / "corpus_sw"))
    ap.add_argument("--work", default=None, help="keep the runs here (default: a temp folder)")
    ap.add_argument("--out", default=str(ROOT / "_out" / "benchmark_layout.json"))
    a = ap.parse_args()
    with tempfile.TemporaryDirectory() as td:
        res = run(Path(a.corpus), Path(a.work or td))
    Path(a.out).write_text(json.dumps(res, indent=1), encoding="utf-8")
    print("ARTIFACT", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
