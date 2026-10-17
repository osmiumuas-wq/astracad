"""Second-tier benchmark corpus: drawings that SOLIDWORKS itself generates from sample PARTS.

    py tools/make_generated_corpus.py [--limit N] [--out _sources/corpus_gen]

For each selected sample part: `mac drawing` makes a drawing with the model's dimensions (standard
3-view, ISO projection), then `mac export-drawing` exports DXF + PDF and writes the answer key from the
SOLIDWORKS API. The key is therefore independent of our readers, as in the first corpus.

This tier is EASIER than the human-drafted sample drawings (dimension placement is automatic, no
title-block clutter, no hand-made notes), so its scores are reported separately and never merged
into the headline figures. Parts are read-only inputs: their SHA-256 is recorded before and after.
Licensed sample files: outputs stay in _sources/ (git-ignored) and are not redistributed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAC = ROOT.parent / "mecagent-alternative" / "src" / "Mac" / "bin" / "Release" / "net10.0-windows" / "mac.exe"
SAMPLES = Path(r"C:\Users\Public\Documents\SOLIDWORKS\SOLIDWORKS 2026\samples")
# folders whose parts carry engineering dimensions (tolerance studies, drawing tutorials, lessons)
FOLDERS = ["tutorial/tolanalyst", "tutorial/dimxpert", "tutorial/advdrawings", "tutorial/lesson1",
           "tutorial/lesson2", "tutorial/mbd", "tutorial/api", "learn/U-Joint", "learn/Mouse"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "_sources" / "corpus_gen"))
    ap.add_argument("--work", default=str(ROOT / "_out" / "gen_work"))
    ap.add_argument("--limit", type=int, default=40)
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    parts = []
    for f in FOLDERS:
        parts += sorted((SAMPLES / f).glob("*.sldprt")) + sorted((SAMPLES / f).glob("*.SLDPRT"))
    seen, chosen = set(), []
    for p in parts:
        k = p.name.lower()
        if k not in seen:
            seen.add(k)
            chosen.append(p)
    manifest_p = out / "manifest.json"
    manifest = json.loads(manifest_p.read_text(encoding="utf-8")) if manifest_p.exists() else {"parts": {}}
    made = 0
    for p in chosen:
        if made >= a.limit:
            break
        name = "g_" + re.sub(r"[^A-Za-z0-9_-]", "_", p.stem)[:50]
        if (out / f"{name}.answer.json").exists() or name in manifest["parts"]:
            continue
        before = sha(p)
        t0 = time.time()
        r1 = subprocess.run([str(MAC), "drawing", str(p), "--out", str(work), "--name", name],
                            capture_output=True, text=True, timeout=300)
        drw = next(iter(work.glob(f"{name}.[sS][lL][dD][dD][rR][wW]")), None)
        rec = {"part": str(p), "sha256": before, "drawing_exit": r1.returncode}
        if drw is None:
            rec["result"] = "no drawing: " + (r1.stdout.strip().splitlines() or ["?"])[-1][:200]
        else:
            r2 = subprocess.run([str(MAC), "export-drawing", str(drw), "--out", str(out), "--name", name],
                                capture_output=True, text=True, timeout=300)
            rec["export_exit"] = r2.returncode
            rec["result"] = (r2.stdout.strip().splitlines() or ["?"])[-1][:200]
            if (out / f"{name}.answer.json").exists():
                made += 1
        rec["sha256_after"] = sha(p)
        rec["part_unchanged"] = rec["sha256_after"] == before
        rec["seconds"] = round(time.time() - t0, 1)
        manifest["parts"][name] = rec
        manifest_p.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        print(f"{name:<40} {rec['result'][:90]}", flush=True)
    print(f"MADE {made} new answer keys in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
