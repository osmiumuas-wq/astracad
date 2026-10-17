"""Score ASTraCAD's drawing readers against answer keys taken from the SOLIDWORKS API.

    py tools/benchmark_sw.py [--corpus _sources/corpus_sw] [--ocr] [--out _out/benchmark_sw.json]

For every `<name>.answer.json` written by `mac export-drawing`, each sheet's exported DXF (and PDF,
and with --ocr a 300 dpi rendering of the PDF) is read by the matching ASTraCAD route and matched
one-to-one against the dimensions SOLIDWORKS reports for that sheet.

Matching rule (stated so it can be disputed): an extracted row matches a key dimension when the
kinds are compatible and |nominal - key value| <= half a unit of the row's printed last digit
(+1e-6). Each key dimension and each row is used at most once, closest first. A row that matches no
key dimension counts against precision; a key dimension nothing matched counts against recall.
Tolerance accuracy is scored only on matched dimensions whose key states a tolerance: the extracted
(min, max) must equal key value + (tol_min, tol_max) within the same half-digit.

The key is the CAD application's own data model, read independently of the exported files. It can
contain dimensions that are not printed (hidden, or dimensions SOLIDWORKS reports per view that a
DXF export flattens); those depress recall and are reported, not filtered.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

KIND_OK = {
    # A linear dimension across a cylinder in a side/section view printed with ⌀ IS a diameter
    # (standard drafting); SOLIDWORKS still types it linear.
    "linear": {"linear", "diameter"}, "ordinate": {"linear"}, "chamfer": {"linear", "angular"},
    "diameter": {"diameter"}, "radius": {"radius"}, "angular": {"angular"},
    "arc_length": {"linear", "radius"},
}
GTOL_SYMBOL = {"STRAIGHT": "straightness", "FLAT": "flatness", "CIRC": "circularity",
               "CYL": "cylindricity", "LPROF": "profile of a line", "SPROF": "profile of a surface",
               "ANGULAR": "angularity", "PERP": "perpendicularity", "PARALLEL": "parallelism",
               "POSITION": "position", "CONC": "concentricity", "SYMMETRY": "symmetry",
               "CIRCRUNOUT": "circular runout", "TOTALRUNOUT": "total runout",
               "RUNOUT": "circular runout"}


def _half_digit(text: str) -> float:
    m = re.findall(r"\d+(?:\.(\d+))?", text or "")
    if not m:
        return 0.5
    dec = len(m[0]) if m[0] else 0
    return 0.5 * 10 ** (-dec)


def score_sheet(key_dims: list[dict], rows: list) -> dict:
    """rows: InspectionRow-like objects with kind, nominal, minimum, maximum, callout.
    Geometric (frame) rows are scored by score_gtols, not here."""
    rows = [r for r in rows if r.kind != "geometric"]
    cand = []
    for i, k in enumerate(key_dims):
        ok_kinds = KIND_OK.get(k["kind"], {"linear"})
        for j, r in enumerate(rows):
            if r.nominal is None or r.kind not in ok_kinds:
                continue
            d = abs(r.nominal - k["value"])
            if d <= _half_digit(r.callout) + 1e-6:
                cand.append((d, i, j))
    cand.sort()
    used_k, used_r, pairs = set(), set(), []
    for d, i, j in cand:
        if i in used_k or j in used_r:
            continue
        used_k.add(i)
        used_r.add(j)
        pairs.append((i, j))
    tol_checked = tol_ok = 0
    tol_wrong = []
    for i, j in pairs:
        k, r = key_dims[i], rows[j]
        if k.get("tol_type") in (None, "none", "basic") or k.get("tol_max") is None:
            continue
        tol_checked += 1
        lo, hi = k["value"] + k["tol_min"], k["value"] + k["tol_max"]
        if k.get("tol_type") == "symmetric" and k["tol_min"] > 0:
            lo = k["value"] - k["tol_min"]
        h = _half_digit(r.callout) + 1e-6
        if r.minimum is not None and r.maximum is not None and abs(r.minimum - lo) <= h \
                and abs(r.maximum - hi) <= h:
            tol_ok += 1
        else:
            tol_wrong.append(f"{r.callout!r}: read {r.minimum}..{r.maximum}, key {lo:g}..{hi:g} "
                             f"({k['tol_type']})")
    return {
        "key": len(key_dims), "rows": len(rows), "matched": len(pairs),
        "missed": [f"{k['kind']} {k['value']:.4g} {k.get('tol_type', '')}"
                   for i, k in enumerate(key_dims) if i not in used_k],
        "unmatched_rows": [r.callout for j, r in enumerate(rows) if j not in used_r],
        "tol_checked": tol_checked, "tol_ok": tol_ok, "tol_wrong": tol_wrong,
    }


def score_gtols(key_gtols: list[dict], rows: list) -> dict:
    want = []
    for g in key_gtols:
        for f in g["frames"]:
            sym = next((s for s in (f.get("symbols") or []) if s), "")
            code = re.sub(r"[<>]|IGTOL-", "", sym).upper()
            vals = [v for v in (f.get("values") or []) if v]
            want.append((GTOL_SYMBOL.get(code, code.lower()), vals[0] if vals else None))
    got = [(r.callout.split(" | ")[0], r.maximum) for r in rows if r.kind == "geometric"]
    hit = 0
    pool = list(got)
    for sym, val in want:
        m = next((g for g in pool if g[0] == sym and val and g[1] is not None
                  and abs(g[1] - float(re.sub(r"[^\d.]", "", val) or "nan")) < 1e-9), None)
        if m:
            pool.remove(m)
            hit += 1
    return {"key_frames": len(want), "found": hit, "key": want, "read": got}


def run(corpus: Path, ocr: bool) -> dict:
    from astracad_inspect.ingest.dxf import read_dxf
    from astracad_inspect.ingest.pdf import read_pdf
    from astracad_inspect.plan import build_plan
    results = []
    for kp in sorted(corpus.glob("*.answer.json")):
        key = json.loads(kp.read_text(encoding="utf-8"))
        for sh in key["sheets"]:
            entry = {"drawing": kp.name[:-len(".answer.json")], "sheet": sh["name"],
                     "key_dimensions": len(sh["dimensions"]),
                     "key_gtol_frames": sum(len(g["frames"]) for g in sh["gtols"]), "routes": {}}
            routes = []
            if "dxf" in sh["files"]:
                routes.append(("dxf", lambda p=corpus / sh["files"]["dxf"]: read_dxf(p)))
            if "pdf" in sh["files"]:
                routes.append(("pdf", lambda p=corpus / sh["files"]["pdf"]: read_pdf(p, key["unit"])))
                if ocr:
                    def _ocr(p=corpus / sh["files"]["pdf"]):
                        from astracad_inspect.ingest.raster import read_raster
                        return read_raster(p, key["unit"])
                    routes.append(("ocr", _ocr))
            for rname, reader in routes:
                t0 = time.time()
                try:
                    plan = build_plan(reader())
                    s = score_sheet(sh["dimensions"], plan.rows)
                    s["gtol"] = score_gtols(sh["gtols"], plan.rows)
                    s["escalated"] = len(plan.uninterpreted)
                except Exception as ex:          # a crash is a result, not a skipped sheet
                    s = {"error": f"{type(ex).__name__}: {ex}"}
                s["seconds"] = round(time.time() - t0, 1)
                entry["routes"][rname] = s
            results.append(entry)
    agg = {}
    for r in results:
        for rn, s in r["routes"].items():
            a = agg.setdefault(rn, {"sheets": 0, "errors": 0, "key": 0, "rows": 0, "matched": 0,
                                    "tol_checked": 0, "tol_ok": 0, "gtol_key": 0, "gtol_found": 0})
            a["sheets"] += 1
            if "error" in s:
                a["errors"] += 1
                continue
            for k in ("key", "rows", "matched", "tol_checked", "tol_ok"):
                a[k] += s[k]
            a["gtol_key"] += s["gtol"]["key_frames"]
            a["gtol_found"] += s["gtol"]["found"]
    for a in agg.values():
        a["recall"] = round(a["matched"] / a["key"], 4) if a["key"] else None
        a["precision"] = round(a["matched"] / a["rows"], 4) if a["rows"] else None
        a["tolerance_accuracy"] = round(a["tol_ok"] / a["tol_checked"], 4) if a["tol_checked"] else None
    return {"sheets": results, "aggregate": agg}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=str(ROOT / "_sources" / "corpus_sw"))
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "_out" / "benchmark_sw.json"))
    a = ap.parse_args()
    res = run(Path(a.corpus), a.ocr)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    for r in res["sheets"]:
        line = f"{r['drawing'][:18]:<18} {r['sheet'][:10]:<10} key={r['key_dimensions']:<3} gtol={r['key_gtol_frames']:<2}"
        for rn, s in r["routes"].items():
            line += (f" | {rn}: ERROR" if "error" in s else
                     f" | {rn}: {s['matched']}/{s['key']} rows={s['rows']} tol={s['tol_ok']}/{s['tol_checked']}")
        print(line)
    for rn, a2 in res["aggregate"].items():
        print(f"AGGREGATE {rn}: {json.dumps(a2)}")
    print(f"REPORT    {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
