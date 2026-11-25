"""Prove the judged path works on a machine that has nothing installed.

Ten checks, each one a way a judge laptop could refuse to run this: no network, no third-party
imports, a console that cannot print the diameter sign, a path with spaces, an edit that must be
caught. The exit status is the answer: 0 means the entry is safe to demo.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from astracad_decide import engine, model  # noqa: E402
from astracad_decide.cli import utf8_console  # noqa: E402

SHA = "a" * 64


def _plan():
    base = {"kind": "linear", "nominal": 10.0, "unit": "mm", "tolerance": {}, "count": 1,
            "state": "parsed", "review": "detected", "source": {"entity_handle": "1F"},
            "geometry_value": None, "balloon_no": 1, "checks": [], "issues": [],
            "history": []}
    ok = dict(base, cid="CH-001", raw_callout="\u230010 +/-0.05", limits=(9.95, 10.05))
    ok["checks"] = [{"name": "geometry_cross_check", "outcome": "passed",
                     "expected": "10.000000", "actual": "10.000000", "why": None,
                     "delta": 0.0}]
    bad = dict(base, cid="CH-002", raw_callout="20 +/-0.10", limits=(19.9, 20.1))
    bad["checks"] = [{"name": "geometry_cross_check", "outcome": "failed",
                      "expected": "20.000000", "actual": "20.400000",
                      "why": "measured off nominal", "delta": 0.4}]
    unknown = dict(base, cid="CH-003", raw_callout="R0.09", limits=None,
                   state="unresolved")
    return {"source_document": "C:/work/plate.dxf", "source_sha256": SHA,
            "revision": "B", "revision_source": "stated on the drawing", "unit": "mm",
            "page_count": 1, "characteristics": [ok, bad, unknown], "rows": [],
            "checks": [], "issues": [], "uninterpreted": [],
            "layout": {"unplaced": [], "balloons": [], "pages": 1}, "coverage": {},
            "title_block": {}, "notes": [], "warnings": [], "approvable": True,
            "gate": "releasable", "accepted_exceptions": []}


def check_python() -> str:
    v = sys.version_info
    if v < (3, 10):
        raise AssertionError(f"python {v.major}.{v.minor} is too old; the layer needs 3.10+")
    return f"{v.major}.{v.minor}.{v.micro}"


def check_stdlib_only() -> str:
    bad, files = set(), 0
    for path in sorted((ROOT / "src" / "astracad_decide").glob("*.py")):
        files += 1
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [] if node.level else [(node.module or "").split(".")[0]]
            else:
                continue
            bad.update(n for n in names if n and n not in sys.stdlib_module_names
                       and n != "astracad_decide")
    if bad:
        raise AssertionError("third-party import(s): " + ", ".join(sorted(bad)))
    return f"{files} modules, standard library only"


def check_no_network() -> str:
    real = socket.socket

    def refuse(*a, **k):
        raise AssertionError("the decision layer tried to open a socket")

    socket.socket = refuse
    try:
        engine.evaluate(_plan(), actor="K M")
    finally:
        socket.socket = real
    return "ruled with sockets disabled"


def check_deterministic() -> str:
    a = engine.evaluate(_plan(), actor="K M")
    b = engine.evaluate(_plan(), actor="K M")
    if a.decision_id != b.decision_id:
        raise AssertionError(f"two rulings, two ids: {a.decision_id} and {b.decision_id}")
    if a.hash != b.hash:
        raise AssertionError("two rulings, two hashes")
    return a.decision_id


def check_spaces_in_path() -> str:
    with tempfile.TemporaryDirectory(prefix="pre flight ") as td:
        p = Path(td) / "a b" / "decisions.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        store = model.DecisionStore(p)
        store.append(engine.evaluate(_plan(), actor="K M"))
        defects = store.verify()
        if defects:
            raise AssertionError("; ".join(defects))
    return "append and verify under a path with spaces"


def _env(**extra) -> dict:
    src = str(ROOT / "src")
    env = dict(os.environ, PYTHONPATH=src + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.update(extra)
    return env


def _write_plan(d: Path) -> Path:
    p = d / "plan.json"
    p.write_text(json.dumps(_plan()), encoding="utf-8")
    return p


def _decide(plan_path: Path, store: Path, actor: str) -> subprocess.CompletedProcess:
    env = _env(PYTHONIOENCODING="cp1252")
    return subprocess.run(
        [sys.executable, "-m", "astracad_decide", "decide", str(plan_path),
         "--actor", actor, "--store", str(store), "--json"],
        capture_output=True, env=env, cwd=str(ROOT))


def check_console() -> str:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        r = _decide(_write_plan(td), td / "d.jsonl", "K M")
        if r.returncode != 0:
            tail = (r.stderr or b"").decode("utf-8", "replace").strip()[-300:]
            raise AssertionError(f"exit {r.returncode}: {tail}")
    return "decide --json prints the diameter sign on a cp1252 console"


def check_refusal() -> str:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        r = _decide(_write_plan(td), td / "d.jsonl", "")
        if r.returncode != 2:
            raise AssertionError(f"a blank actor exited {r.returncode}, not 2")
    return "a ruling with no named actor is refused (exit 2)"


def check_tamper() -> str:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        store = td / "d.jsonl"
        if _decide(_write_plan(td), store, "K M").returncode != 0:
            raise AssertionError("the honest ruling did not run")
        text = store.read_text(encoding="utf-8")
        store.write_text(text.replace("K M", "K N", 1), encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "astracad_decide", "verify", str(store)],
                           capture_output=True, env=_env(), cwd=str(ROOT))
        out = (r.stdout + r.stderr).decode("utf-8", "replace")
        if r.returncode != 4:
            raise AssertionError(f"an edited record exited {r.returncode}, not 4")
        if "hash does not recompute" not in out:
            raise AssertionError("the defect was not named: " + out.strip()[-200:])
    return "one edited character is caught and named (exit 4)"


def check_no_stray_writes() -> str:
    with tempfile.TemporaryDirectory(prefix="stray ") as td:
        cwd = Path(td) / "cwd"
        cwd.mkdir()
        plan = _write_plan(Path(td))
        r = subprocess.run(
            [sys.executable, "-m", "astracad_decide", "decide", str(plan),
             "--actor", "K M", "--store", str(Path(td) / "d.jsonl")],
            capture_output=True, env=_env(), cwd=str(cwd))
        if r.returncode != 0:
            raise AssertionError(f"exit {r.returncode}")
        left = sorted(x.name for x in cwd.iterdir())
        if left:
            raise AssertionError("wrote into the working directory: " + ", ".join(left))
    return "nothing written outside the named store"


def check_utf8() -> str:
    d = engine.evaluate(_plan(), actor="K M")
    text = json.dumps(d.to_dict(), ensure_ascii=False, sort_keys=True)
    if text.encode("utf-8").decode("utf-8") != text:
        raise AssertionError("the record does not survive a utf-8 round trip")
    if "\u2300" not in text:
        raise AssertionError("the diameter sign is missing from the record")
    return "unicode survives the record file"


def check_no_secrets() -> str:
    listed = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True)
    if listed.returncode != 0:
        raise AssertionError("git ls-files failed: " + listed.stderr.strip()[:120])
    paths = [p for p in listed.stdout.splitlines() if p.strip()]
    patterns = ("sk-ant-", "sk-proj-", "ghp_", "AKIA", "BEGIN PRIVATE KEY", "BEGIN OPENSSH PRIVATE KEY")
    hits = []
    for rel in paths:
        path = ROOT / rel
        try:
            if path.stat().st_size > 512_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except (OSError, ValueError):
            continue
        hits.extend(f"{rel}: {pat}" for pat in patterns if pat in text)
    if hits:
        raise AssertionError("key-shaped string(s) in the tree: " + "; ".join(hits[:5]))
    return f"{len(paths)} tracked files, no key-shaped strings"


CHECKS = [
    ("python", check_python),
    ("stdlib only", check_stdlib_only),
    ("offline", check_no_network),
    ("deterministic ids", check_deterministic),
    ("spaces in paths", check_spaces_in_path),
    ("cp1252 console", check_console),
    ("refuses blank actor", check_refusal),
    ("tamper is caught", check_tamper),
    ("no stray writes", check_no_stray_writes),
    ("no secrets tracked", check_no_secrets),
    ("utf-8 record", check_utf8),
]


def main(argv=None) -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable results")
    args = ap.parse_args(argv)

    results = []
    for name, fn in CHECKS:
        start = time.perf_counter()
        try:
            detail, ok = fn(), True
        except Exception as exc:  # noqa: BLE001 - a failure is the result
            detail, ok = f"{type(exc).__name__}: {exc}", False
        results.append({"check": name, "ok": ok, "detail": detail,
                        "seconds": round(time.perf_counter() - start, 3)})

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("ASTraCAD Omega - judged-path preflight")
        print("")
        for r in results:
            mark = "PASS" if r["ok"] else "FAIL"
            print("  %s  %-20s %s  (%.2fs)" % (mark, r["check"], r["detail"], r["seconds"]))
        passed = sum(1 for r in results if r["ok"])
        print("")
        print(f"  {passed}/{len(results)} checks passed")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())