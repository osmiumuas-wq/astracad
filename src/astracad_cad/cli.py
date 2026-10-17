"""astracad-cad: status | declare | interpret | build | spec | edit | inspect | reopen | tasks

Exit codes: 0 succeeded and verified; 3 executed but not verified / waiting; 2 usage or the
request is not executable; 1 failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from astracad_core.requests import interpret
from astracad_core.tasks import TaskStore

from .adapters import ALL
from .contract import Authorization, Op, OpStatus
from .orchestrator import CadRequest, Orchestrator
from .propose import proposal_route, propose_for

DEFAULT_DB = Path.home() / ".astracad" / "tasks.sqlite3"


def _orch(db: str) -> Orchestrator:
    return Orchestrator({k: cls() for k, cls in ALL.items()}, TaskStore(db))


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


_EXIT = {OpStatus.SUCCEEDED: 0, OpStatus.EXECUTED_UNVERIFIED: 3,
         OpStatus.REQUIRES_AUTHORIZATION: 3}


def _run_edit(a, source: str, sets: dict, name: str, summary: str) -> int:
    out = str(Path(a.out).resolve())
    expect = {}
    if getattr(a, "expect_size", None):
        expect["size_mm"] = a.expect_size
    if getattr(a, "expect_volume", None) is not None:
        expect["volume_mm3"] = a.expect_volume
    auth = None
    if a.authorize:
        auth = Authorization(a.authorize, frozenset({Op.MODIFY_FEATURE}), (out,),
                             note="granted on the command line for one edit of a copy")
    req = CadRequest("solidworks", Op.MODIFY_FEATURE,
                     {"source_path": str(Path(source).resolve()), "set": sets, "name": name,
                      "out_dir": out, "expect": expect}, summary=summary)
    tid, res = _orch(a.db).submit(req, auth)
    print(f"TASK {tid}")
    if res is None:
        return 1
    _print(res.to_dict())
    return _EXIT.get(res.status, 1)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="astracad-cad")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="connection state of every CAD adapter")
    d = sub.add_parser("declare", help="print an adapter's capability declaration")
    d.add_argument("app", choices=sorted(ALL))
    i = sub.add_parser("interpret", help="parse a request; never executes")
    i.add_argument("request")
    b = sub.add_parser("build", help="interpret a request and execute it if fully specified")
    b.add_argument("request")
    b.add_argument("--app", default=None)
    b.add_argument("--out", required=True)
    b.add_argument("--authorize", metavar="NAME", default=None,
                   help="your name: authorises this one create run and writes under --out")
    b.add_argument("--expect-size", nargs=3, type=float, dest="expect_size")
    b.add_argument("--expect-volume", type=float, dest="expect_volume")
    sp = sub.add_parser("spec", help="execute a typed part spec (JSON) through the permission gate")
    sp.add_argument("path")
    sp.add_argument("--app", default="solidworks")
    sp.add_argument("--out", required=True)
    sp.add_argument("--authorize", metavar="NAME", default=None)
    sp.add_argument("--expect-size", nargs=3, type=float, dest="expect_size")
    sp.add_argument("--expect-volume", type=float, dest="expect_volume")
    e = sub.add_parser("edit", help="edit a COPY of an existing .sldprt (source is never written)")
    e.add_argument("source")
    e.add_argument("--set", action="append", required=True, metavar="NAME=MM")
    e.add_argument("--name", required=True, help="file name of the edited copy")
    e.add_argument("--out", required=True)
    e.add_argument("--authorize", metavar="NAME", default=None)
    e.add_argument("--expect-size", nargs=3, type=float, dest="expect_size")
    e.add_argument("--expect-volume", type=float, dest="expect_volume")
    dr = sub.add_parser("drawing", help="create a drawing of an existing .sldprt (views + model "
                                        "dimensions), verified by inspecting its DXF")
    dr.add_argument("part")
    dr.add_argument("--name", required=True)
    dr.add_argument("--out", required=True)
    dr.add_argument("--angle", choices=("first", "third"), default="first")
    dr.add_argument("--authorize", metavar="NAME", default=None)
    for opt in ("title", "drawing-number", "revision", "material", "tolerance"):
        dr.add_argument(f"--{opt}", default=None)   # unstated revision / tolerance: reported missing
    ins = sub.add_parser("inspect", help="read dimensions and mass of an existing .sldprt")
    ins.add_argument("path")
    r = sub.add_parser("reopen", help="independently re-read an exported STEP file")
    r.add_argument("path")
    r.add_argument("--size", nargs=3, type=float)
    r.add_argument("--volume", type=float)
    t = sub.add_parser("tasks", help="list recorded tasks")
    t.add_argument("--id")
    t.add_argument("--recover", action="store_true",
                   help="move tasks left RUNNING by a crash or restart to RECOVERING")
    a = ap.parse_args(argv)

    if a.cmd == "status":
        rows = _orch(a.db).status_all()
        for s in rows:
            print(f"{s['application']:<22} {s['state']:<20} {s.get('version') or '-':<16} "
                  f"{s['detail']}")
        return 0
    if a.cmd == "declare":
        _print(ALL[a.app]().declaration().to_dict())
        return 0
    if a.cmd == "interpret":
        _print(interpret(a.request).to_dict())
        return 0
    if a.cmd == "reopen":
        expect = {}
        if a.size:
            expect["size_mm"] = a.size
        if a.volume is not None:
            expect["volume_mm3"] = a.volume
        res = ALL["solidworks"]().execute(Op.REOPEN_EXPORT, {"path": a.path, "expect": expect})
        _print(res.to_dict())
        return 0 if res.ok else 3
    if a.cmd == "tasks":
        store = TaskStore(a.db)
        if a.recover:
            for tid in store.recover_interrupted():
                print(f"RECOVERING {tid}")
        if a.id:
            t = store.get(a.id)
            _print(t.__dict__)
        else:
            for t in store.list():
                print(f"{t.task_id}  {t.state.value:<22} {t.created_utc}  {t.request}")
        return 0
    if a.cmd == "inspect":
        tid, res = _orch(a.db).submit(CadRequest("solidworks", Op.INSPECT_PARAMETERS,
                                                 {"path": a.path}, summary=f"inspect {a.path}"))
        print(f"TASK {tid}")
        _print(res.to_dict())
        return 0 if res.ok else 3
    if a.cmd == "drawing":
        out = str(Path(a.out).resolve())
        auth = (Authorization(a.authorize, frozenset({Op.CREATE_DRAWING}), (out,),
                              note="granted on the command line for one drawing")
                if a.authorize else None)
        tid, res = _orch(a.db).submit(CadRequest(
            "solidworks", Op.CREATE_DRAWING,
            {"part_path": str(Path(a.part).resolve()), "name": a.name, "out_dir": out,
             "angle": a.angle, "title": a.title, "drawing_number": a.drawing_number,
             "revision": a.revision, "material": a.material, "tolerance": a.tolerance},
            summary=f"drawing of {a.part}"), auth)
        print(f"TASK {tid}")
        if res is None:
            return 1
        _print(res.to_dict())
        return _EXIT.get(res.status, 1)
    if a.cmd == "spec":
        try:
            spec = json.loads(Path(a.path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as ex:
            print(f"bad spec {a.path}: {ex}")
            return 2
        out = str(Path(a.out).resolve())
        auth = (Authorization(a.authorize, frozenset({Op.CREATE_FEATURE}), (out,),
                              note="granted on the command line for one spec run")
                if a.authorize else None)
        expect = {}
        if a.expect_size:
            expect["size_mm"] = a.expect_size
        if a.expect_volume is not None:
            expect["volume_mm3"] = a.expect_volume
        tid, res = _orch(a.db).submit(CadRequest(a.app, Op.CREATE_FEATURE,
                                                 {"spec": spec, "out_dir": out, "expect": expect},
                                                 summary=f"spec {a.path}"), auth)
        print(f"TASK {tid}")
        if res is None:
            return 1
        _print(res.to_dict())
        return _EXIT.get(res.status, 1)
    if a.cmd == "edit":
        sets = {}
        for kv in a.set:
            k, _, v = kv.partition("=")
            try:
                sets[k] = float(v)
            except ValueError:
                print(f"bad --set {kv}")
                return 2
        return _run_edit(a, a.source, sets, a.name, f"edit {a.source} {sets}")
    if a.cmd == "build":
        it = interpret(a.request, default_app=a.app)
        _print({"interpretation": it.to_dict()})
        if not it.executable and proposal_route(it):
            tid, res, prop = propose_for(it, _orch(a.db))
            print(f"TASK {tid}")
            if prop is None:
                _print(res.to_dict() if res is not None else {"error": "inspect did not run"})
                print("NOT EXECUTED: the part could not be inspected, so no candidates exist")
                return 1 if res is None else 3
            _print({"proposal": prop})
            print("NOT EXECUTED: choose a dimension; nothing was selected or changed")
            return 3
        if not it.executable:
            print("NOT EXECUTED: the request is incomplete or ambiguous (see missing/ambiguities)")
            return 2
        if it.intent == "modify_existing":
            return _run_edit(a, it.spec["source_path"], it.spec["set"], it.spec["name"],
                             it.request)
        if it.intent != "create_part":
            print(f"NOT EXECUTED: intent {it.intent} has no executable route")
            return 2
        out = str(Path(a.out).resolve())
        auth = None
        if a.authorize:
            auth = Authorization(a.authorize, frozenset({Op.CREATE_FEATURE}), (out,),
                                 note="granted on the command line for one build")
        expect = dict(it.expect)
        if a.expect_size:
            expect["size_mm"] = a.expect_size
        if a.expect_volume is not None:
            expect["volume_mm3"] = a.expect_volume
        req = CadRequest(it.application, Op.CREATE_FEATURE,
                         {"spec": it.spec, "out_dir": out, "expect": expect},
                         summary=it.request)
        tid, res = _orch(a.db).submit(req, auth)
        print(f"TASK {tid}")
        if res is None:
            return 1
        _print(res.to_dict())
        return {OpStatus.SUCCEEDED: 0, OpStatus.EXECUTED_UNVERIFIED: 3,
                OpStatus.REQUIRES_AUTHORIZATION: 3}.get(res.status, 1)
    return 2
