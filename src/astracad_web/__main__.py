"""py -m astracad_web [--runs DIR ...] [--db PATH] [--port 8765]

Serves the workspace on http://127.0.0.1:<port>/ only. --runs folders are scanned for inspection
runs (*.plan.json) and change sets (*.changes.json); the first one also receives new runs.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .server import Workspace, serve


def main(argv: list[str] | None = None) -> int:
    from astracad_cad.cli import DEFAULT_DB, _orch

    ap = argparse.ArgumentParser(prog="astracad-web")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="task database (same as astracad_cad)")
    ap.add_argument("--runs", action="append", default=None,
                    help="folder of inspection runs / change sets (repeatable; first gets new runs)")
    ap.add_argument("--projects", default=str(Path.home() / ".astracad" / "projects"),
                    help="project store: uploaded drawings are preserved here (read-only, by SHA-256)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-cad-status", action="store_true",
                    help="do not probe CAD applications (faster start, no COM access)")
    a = ap.parse_args(argv)
    runs = a.runs or [str(Path.cwd() / "_out" / "web")]
    ws = Workspace(a.db, runs, None if a.no_cad_status else _orch, projects=a.projects)
    httpd = serve(ws, "127.0.0.1", a.port)
    print(f"WORKSPACE  http://127.0.0.1:{a.port}/   (local only; Ctrl+C to stop)")
    print(f"TASKS      {a.db}")
    for r in ws.runs:
        print(f"RUNS       {r}")
    print(f"PROJECTS   {ws.projects.root}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
