"""Local web workspace over the ASTraCAD engines.

One process, standard library only, bound to 127.0.0.1. It shows what the CLIs already produce —
CAD connection status, the persistent task history, inspection runs and drawing change sets — and
offers exactly one action: run an inspection on a drawing file. CAD-modifying operations are not
exposed here; they stay on the CLI behind an explicit --authorize.

Local-only hardening (a browser can be made to talk to localhost by any web page):
- the Host header must be localhost / 127.0.0.1 (defeats DNS rebinding);
- a POST must carry the custom header X-AstraCAD: 1, which a cross-site form cannot send and a
  cross-site fetch cannot send without a CORS preflight this server never approves;
- files are served only from inside the configured run folders, after resolving symlinks and "..".
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from astracad_core.tasks import TaskStore

PAGE = Path(__file__).with_name("index.html")
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
_MAX_DEPTH = 4


class Workspace:
    """Everything the pages read. Separate from HTTP so it can be tested without a socket."""

    def __init__(self, db: str | Path, runs: list[str | Path], orchestrator_factory=None,
                 status_ttl_s: float = 30.0, projects: str | Path | None = None):
        self.db = Path(db)
        self.runs = [Path(r).resolve() for r in runs]
        self.runs[0].mkdir(parents=True, exist_ok=True)
        # projects: preserved source documents + their runs; the root is also a run folder, so
        # project runs appear in every list and their files resolve through the same keys
        from astracad_core.projects import ProjectStore
        self.projects = ProjectStore(projects) if projects else None
        if self.projects is not None and self.projects.root not in self.runs:
            self.runs.append(self.projects.root)
        self._orch_factory = orchestrator_factory
        self._status: tuple[float, list] | None = None
        self._status_ttl = status_ttl_s
        self._lock = threading.Lock()

    # ---- CAD status ---------------------------------------------------------------------------
    def status(self, refresh: bool = False) -> dict:
        with self._lock:
            now = time.time()
            if refresh or self._status is None or now - self._status[0] > self._status_ttl:
                rows = []
                if self._orch_factory is not None:
                    for s in self._orch_factory(str(self.db)).status_all():
                        rows.append({k: s.get(k) for k in ("application", "state", "version", "detail")})
                self._status = (now, rows)
            return {"checked_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._status[0])),
                    "applications": self._status[1]}

    # ---- tasks --------------------------------------------------------------------------------
    def tasks(self) -> list[dict]:
        if not self.db.exists():
            return []
        return [{"task_id": t.task_id, "state": t.state.value, "request": t.request,
                 "created_utc": t.created_utc, "updated_utc": t.updated_utc,
                 "outcome": t.outcome, "retries": t.retries}
                for t in TaskStore(self.db).list()]

    def task(self, task_id: str) -> dict:
        t = TaskStore(self.db).get(task_id)
        d = dict(t.__dict__)
        d["state"] = t.state.value
        return d

    # ---- run folders --------------------------------------------------------------------------
    def _walk(self, pattern: str):
        seen = set()
        for root in self.runs:
            if not root.exists():
                continue
            for p in sorted(root.rglob(pattern)):
                try:
                    depth = len(p.relative_to(root).parts)
                except ValueError:
                    continue
                if depth <= _MAX_DEPTH and p.resolve() not in seen:
                    seen.add(p.resolve())
                    yield root, p

    def key_for(self, path: Path) -> str:
        for i, root in enumerate(self.runs):
            try:
                return f"{i}/{path.resolve().relative_to(root).as_posix()}"
            except ValueError:
                continue
        raise ValueError("path is outside the run folders")

    def resolve_key(self, key: str) -> Path:
        """Map a key back to a file, refusing anything that escapes its run folder."""
        idx, _, rel = unquote(key).partition("/")
        if not idx.isdigit() or int(idx) >= len(self.runs) or not rel:
            raise PermissionError("unknown run folder")
        root = self.runs[int(idx)]
        p = (root / rel).resolve()
        if root not in p.parents or not p.is_file():
            raise PermissionError("not a file inside the run folder")
        return p

    @staticmethod
    def _read(p: Path) -> Any:
        return json.loads(p.read_text(encoding="utf-8"))

    def inspections(self) -> list[dict]:
        out = []
        for _, plan_path in self._walk("*.plan.json"):
            stem = plan_path.name[:-len(".plan.json")]
            try:
                plan = self._read(plan_path)
            except (OSError, ValueError):
                continue
            prov_path = plan_path.with_name(f"{stem}.provenance.json")
            prov = self._read(prov_path) if prov_path.exists() else {}
            ver = prov.get("export_verification") or []
            rows = plan.get("rows") or []
            out.append({
                "key": self.key_for(plan_path), "name": stem,
                "folder": str(plan_path.parent), "document": plan.get("source_document"),
                "route": prov.get("route", "?"), "revision": plan.get("revision"),
                "unit": plan.get("unit"), "rows": len(rows),
                "needs_review": sum(1 for r in rows if r.get("status") == "needs_review"),
                "blocking": sum(1 for r in rows if r.get("blocking")),
                "escalated": len(plan.get("uninterpreted") or []),
                "approvable": plan.get("approvable"), "gate": plan.get("gate"),
                "exports_passed": sum(1 for c in ver if c.get("outcome") == "passed"),
                "exports_total": len(ver), "created_utc": prov.get("created_utc"),
                "project": self._project_of(plan_path),
                "modified": plan_path.stat().st_mtime,
            })
        return sorted(out, key=lambda r: -r["modified"])

    def inspection(self, key: str) -> dict:
        plan_path = self.resolve_key(key)
        if not plan_path.name.endswith(".plan.json"):
            raise PermissionError("not an inspection plan")
        stem = plan_path.name[:-len(".plan.json")]
        plan = self._read(plan_path)
        prov_path = plan_path.with_name(f"{stem}.provenance.json")
        files = {}
        for label, suffix in (("layout", "_layout.png"), ("sheet", "_sheet.png"),
                              ("report", "_inspection_report.pdf"),
                              ("workbook", "_inspection_table.xlsx")):
            p = plan_path.with_name(stem + suffix)
            if p.exists():
                files[label] = self.key_for(p)
        sheet_path = plan_path.with_name(f"{stem}.sheet.json")
        if not sheet_path.exists():
            self._upgrade_sheet(plan_path, plan, prov_path)
        sheet = self._read(sheet_path) if sheet_path.exists() else None
        pages = []
        if sheet:
            for pg in sheet.get("pages") or [{"page": 1, "image": sheet.get("image") or f"{stem}_sheet.png",
                                               "extents": sheet["extents"], "width_px": sheet["width_px"],
                                               "height_px": sheet["height_px"]}]:
                img = plan_path.with_name(pg["image"])
                if img.exists():
                    pages.append({"page": pg["page"], "extents": pg["extents"], "width_px": pg["width_px"],
                                  "height_px": pg["height_px"], "key": self.key_for(img)})
        for lp in sorted(plan_path.parent.glob(f"{stem}_layout_p*.png")):
            files[lp.stem[len(stem) + 1:]] = self.key_for(lp)
        pid = self._project_of(plan_path)
        project = None
        if pid and self.projects is not None:
            try:
                project = {"id": pid, "name": self.projects.get(pid).name}
            except (OSError, ValueError, KeyError):
                project = None
        return {"key": key, "name": stem, "project": project, "plan": {k: plan.get(k) for k in (
                    "source_document", "source_sha256", "revision", "unit", "rows", "checks",
                    "uninterpreted", "gate", "approvable", "warnings", "feature_support",
                    "layout", "title_block", "coverage", "open_gaps")},
                "sheet": {"pages": pages, **{k: sheet.get(k) for k in ("extents", "width_px", "height_px")}}
                if pages else None,
                # per-characteristic evidence for the detail panel: what was checked and who did what
                "evidence": {c["cid"]: {
                    "checks": c.get("checks") or [], "history": c.get("history") or [],
                    "geometry_value": c.get("geometry_value"), "review": c.get("review"),
                    "state": c.get("state"),
                    "source": {k: (c.get("source") or {}).get(k) for k in (
                        "anchor_x", "anchor_y", "zone", "entity_type", "entity_handle", "layer",
                        "page")}} for c in plan.get("characteristics") or [] if c.get("cid")},
                "provenance": self._read(prov_path) if prov_path.exists() else None,
                "files": files}

    def _upgrade_sheet(self, plan_path: Path, plan: dict, prov_path: Path) -> None:
        """Runs made before the interactive sheet existed: render it now, from the same drawing,
        in the frame the run's own layout used. Only when the drawing is byte-identical to the one
        the plan was made from (SHA-256); otherwise the viewer keeps the static image."""
        import hashlib
        stem = plan_path.name[:-len(".plan.json")]
        try:
            src = Path(plan.get("source_document") or "")
            if not src.is_absolute():
                meta_p = plan_path.with_name(f"{stem}.run.json")
                meta = self._read(meta_p) if meta_p.exists() else {}
                src = Path(meta.get("drawing") or src)
            if not src.is_file() or hashlib.sha256(src.read_bytes()).hexdigest() != plan.get("source_sha256"):
                return
            route = (self._read(prov_path) if prov_path.exists() else {}).get("route", "dxf-vector")
            lay = plan.get("layout") or {}
            p, pad = lay["page"], float(lay.get("slot_pitch") or 0) / 2
            frame = (p[0] - pad, p[1] - pad, p[2] + pad, p[3] + pad)
            from astracad_inspect.export.render import render_sheet
            png = plan_path.with_name(f"{stem}_sheet.png")
            rec = render_sheet(src, route, frame, png)
            rec.update(image=png.name, generated_later=True,
                       note="rendered when first viewed, for a run made before the interactive sheet")
            plan_path.with_name(f"{stem}.sheet.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
        except Exception:  # noqa: BLE001 - the static layout image remains; nothing else depends on it
            return

    # ---- projects ----------------------------------------------------------------------------
    def _project_of(self, path: Path) -> str | None:
        if self.projects is None:
            return None
        try:
            rel = path.resolve().relative_to(self.projects.root).parts
        except ValueError:
            return None
        return rel[0] if len(rel) > 2 and rel[1] == "runs" else None

    def _need_projects(self):
        if self.projects is None:
            raise FileNotFoundError("projects are not enabled (start with --projects DIR)")
        return self.projects

    def projects_list(self) -> list[dict]:
        if self.projects is None:
            return []
        runs = self.inspections()
        out = []
        for p in self.projects.list():
            mine = [r for r in runs if r.get("project") == p.id]
            out.append({"id": p.id, "name": p.name, "description": p.description,
                        "created_utc": p.created_utc, "sources": len(p.sources), "runs": len(mine),
                        "releasable": sum(1 for r in mine if r["approvable"]),
                        "last_run": max((r["modified"] for r in mine), default=None)})
        return out

    def project(self, pid: str) -> dict:
        ps = self._need_projects()
        p = ps.get(pid)
        d = p.to_dict()
        for s in d["sources"]:
            s["key"] = self.key_for(ps.root / pid / s["relpath"])
        d["runs"] = [r for r in self.inspections() if r.get("project") == pid]
        return d

    def create_project(self, body: dict) -> dict:
        return self._need_projects().create(str(body.get("name", "")),
                                            str(body.get("description") or "")).to_dict()

    def upload(self, pid: str, filename: str, stream, length: int, by: str = "") -> dict:
        from dataclasses import asdict
        rec = self._need_projects().add_source(pid, filename, stream, length, by)
        return {**asdict(rec), "relpath": rec.relpath}

    def inspect_source(self, body: dict) -> dict:
        """Inspect a preserved source: its hash is re-checked first, the run goes into the project,
        and run.json names the project and the exact bytes (SHA-256) it was made from."""
        ps = self._need_projects()
        pid, sha = str(body.get("project", "")), str(body.get("source", ""))
        src = ps.source_path(pid, sha, verify=True)
        return self.run_inspection(str(src), body.get("units"), body.get("revision"),
                                   body.get("general_tolerance"), runs_root=ps.runs_dir(pid),
                                   origin={"project": pid, "source_sha256": sha})

    def changes(self) -> list[dict]:
        out = []
        for _, p in self._walk("*.changes.json"):
            try:
                d = self._read(p)
            except (OSError, ValueError):
                continue
            out.append({"key": self.key_for(p), "name": p.name[:-len(".changes.json")],
                        "old_document": d.get("old_document"), "new_document": d.get("new_document"),
                        "old_revision": d.get("old_revision"), "new_revision": d.get("new_revision"),
                        "summary": d.get("summary"), "needs_review": d.get("needs_review"),
                        "changes": d.get("changes"), "modified": p.stat().st_mtime})
        return sorted(out, key=lambda r: -r["modified"])

    # ---- the one action -----------------------------------------------------------------------
    def run_inspection(self, drawing: str, units: str | None, revision: str | None,
                       general_tolerance: str | None = None, overrides: str | None = None,
                       parent: str | None = None, folder_name: str | None = None,
                       runs_root: Path | None = None, origin: dict | None = None) -> dict:
        from astracad_inspect.pipeline import inspect_drawing
        src = Path(drawing).expanduser()
        if not src.is_file():
            raise FileNotFoundError(f"no such drawing: {src}")
        from astracad_inspect.ingest.raster import RASTER_SUFFIXES
        if src.suffix.lower() not in {".dxf", ".pdf"} | RASTER_SUFFIXES:
            raise ValueError("only .dxf, .pdf and scanned images are read")
        # Stem + type + second is not unique (part.dxf and part.pdf in one second collided and the
        # second run overwrote the first), so a counter makes every run its own folder.
        base = folder_name or f"{src.stem}_{src.suffix.lower().lstrip('.')}_{time.strftime('%Y%m%d-%H%M%S')}"
        root = Path(runs_root) if runs_root else self.runs[0]
        out, n = root / base, 1
        while out.exists():
            n += 1
            out = root / f"{base}_{n}"
        res = inspect_drawing(src, out, declared_unit=units or None, revision=revision or None,
                              general_tolerance=general_tolerance or None, overrides=overrides)
        meta = {"drawing": str(src.resolve()), "units": units or None, "revision": revision or None,
                "general_tolerance": general_tolerance or None, "overrides": overrides,
                "parent": parent, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **(origin or {})}
        (out / f"{src.stem}.run.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return {"key": self.key_for(res.artifacts["plan_json"]), "delivered": res.delivered,
                "approvable": res.plan.approvable, "folder": str(out)}

    # ---- review: approve / reject / correct, then re-issue -----------------------------------
    def _plan_path(self, key: str) -> Path:
        p = self.resolve_key(key)
        if not p.name.endswith(".plan.json"):
            raise PermissionError("not an inspection plan")
        return p

    @staticmethod
    def _review_paths(plan_path: Path) -> tuple[Path, Path]:
        stem = plan_path.name[:-len(".plan.json")]
        return plan_path.with_name(f"{stem}.review.json"), plan_path.with_name(f"{stem}.review_log.jsonl")

    def review_state(self, key: str) -> dict:
        plan_path = self._plan_path(key)
        stem = plan_path.name[:-len(".plan.json")]
        mp = plan_path.with_name(f"{stem}.run.json")
        meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
        al = plan_path.with_name(f"{stem}.review_applied_log.jsonl")
        applied = [json.loads(x) for x in al.read_text(encoding="utf-8").splitlines() if x.strip()]             if al.exists() else []
        rp, lp = self._review_paths(plan_path)
        review = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else {}
        log = [json.loads(x) for x in lp.read_text(encoding="utf-8").splitlines() if x.strip()] \
            if lp.exists() else []
        return {"review": review, "log": log, "parent": meta.get("parent"), "applied_log": applied}

    def review(self, key: str, body: dict) -> dict:
        """Record one reviewer decision in the run's review file (overrides format) and the log.
        The plan itself is never edited: decisions take effect only when the run is re-issued."""
        plan_path = self._plan_path(key)
        reviewer = str(body.get("reviewer", "")).strip()
        if not _REVIEWER.fullmatch(reviewer):
            raise ValueError("reviewer name required (letters, digits, space . ' - ; up to 64)")
        action = body.get("action")
        reason = str(body.get("reason") or "")[:500]
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        rp, lp = self._review_paths(plan_path)
        review = json.loads(rp.read_text(encoding="utf-8")) if rp.exists() else \
            {"characteristics": [], "annotations": []}
        if action in ("approve", "reject", "correct", "clear"):
            cid = str(body.get("cid", ""))
            if cid not in {c["cid"] for c in plan.get("characteristics") or []}:
                raise ValueError(f"unknown characteristic {cid!r}")
            entry = {"cid": cid, "reviewer": reviewer}
            if action == "approve":
                entry["approve"] = True
            elif action == "reject":
                if not reason:
                    raise ValueError("a rejection needs a reason")
                entry.update(reject=True, reason=reason)
            elif action == "correct":
                entry.update(_correction(body))
            before = next((e for e in review["characteristics"] if e.get("cid") == cid), None)
            review["characteristics"] = [e for e in review["characteristics"] if e.get("cid") != cid]
            if action != "clear":
                review["characteristics"].append(entry)
            target = cid
        elif action in ("dismiss", "as_characteristic"):
            text = str(body.get("text", ""))
            if text not in {u.get("text") for u in plan.get("uninterpreted") or []}:
                raise ValueError("unknown escalated annotation")
            entry = {"text": text, "action": action, "reviewer": reviewer, "reason": reason}
            if action == "as_characteristic":
                entry.update(_correction(body, need_kind=True))
            before = next((e for e in review["annotations"] if e.get("text") == text), None)
            review["annotations"] = [e for e in review["annotations"] if e.get("text") != text]
            review["annotations"].append(entry)
            target = text
        else:
            raise ValueError(f"unsupported review action {action!r}")
        rp.write_text(json.dumps(review, indent=1, ensure_ascii=False), encoding="utf-8")
        with lp.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                "reviewer": reviewer, "action": action, "target": target,
                                "before": before, "after": entry if action != "clear" else None,
                                "reason": reason}, ensure_ascii=False) + "\n")
        return self.review_state(key)

    def reissue(self, key: str) -> dict:
        """A NEW run from the same drawing with the review applied. Refused when the drawing has
        changed since the plan was made: the review's characteristic IDs would not mean the same."""
        import hashlib
        plan_path = self._plan_path(key)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        rp, _ = self._review_paths(plan_path)
        if not rp.exists():
            raise ValueError("nothing to re-issue: no review decisions recorded")
        stem = plan_path.name[:-len(".plan.json")]
        mp = plan_path.with_name(f"{stem}.run.json")
        meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
        drawing = Path(meta.get("drawing") or plan["source_document"])
        if not drawing.is_file():
            raise FileNotFoundError(f"source drawing not found: {drawing}")
        sha = hashlib.sha256(drawing.read_bytes()).hexdigest()
        if sha != plan.get("source_sha256"):
            raise ValueError("the drawing changed since this plan was made; re-run and review again")
        n = 2
        base = plan_path.parent.name.split("_rev")[0]
        root = plan_path.parent.parent
        while (root / f"{base}_rev{n}").exists():
            n += 1
        res = self.run_inspection(str(drawing), meta.get("units") or plan.get("unit"),
                                  meta.get("revision"), meta.get("general_tolerance"),
                                  overrides=str(rp), parent=key, folder_name=f"{base}_rev{n}",
                                  runs_root=root, origin={k: meta[k] for k in ("project", "source_sha256")
                                                          if k in meta})
        # the new run carries the decisions it was made from, so its own folder is the full record
        new_dir = Path(res["folder"])
        import shutil
        shutil.copy(rp, new_dir / f"{stem}.review_applied.json")
        lp = plan_path.with_name(f"{stem}.review_log.jsonl")
        if lp.exists():
            shutil.copy(lp, new_dir / f"{stem}.review_applied_log.jsonl")
        return res


_REVIEWER = re.compile(r"[\w .'-]{1,64}")
_TOL_KINDS = {"none": (), "symmetric": ("plus_minus",), "deviations": ("upper", "lower"),
              "limit_pair": ("maximum", "minimum")}


def _num(v, name):
    import math
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(x):
        raise ValueError(f"{name} must be finite")
    return x


def _correction(body: dict, need_kind: bool = False) -> dict:
    out = {}
    if body.get("kind") is not None or need_kind:
        if body.get("kind") not in ("linear", "diameter", "radius", "angular"):
            raise ValueError("kind must be linear, diameter, radius or angular")
        out["kind"] = body["kind"]
    if "nominal" in body:
        out["nominal"] = None if body["nominal"] in (None, "") else _num(body["nominal"], "nominal")
    tol = body.get("tolerance")
    if tol is not None:
        kind = tol.get("kind")
        if kind not in _TOL_KINDS:
            raise ValueError(f"tolerance kind must be one of {sorted(_TOL_KINDS)}")
        out["tolerance"] = {"kind": kind, **{f: _num(tol.get(f), f) for f in _TOL_KINDS[kind]}}
    if not out:
        raise ValueError("a correction needs a nominal, a tolerance or a kind")
    return out


def make_handler(ws: Workspace):
    # read per request: a local single-user page, and edits to it then need no restart

    class Handler(BaseHTTPRequestHandler):
        server_version = "astracad-web"

        def log_message(self, fmt, *args):      # quiet; the CLI prints one line per request
            pass

        def _host_ok(self) -> bool:
            h = self.headers.get("Host") or ""
            host = h[:h.index("]") + 1] if h.startswith("[") and "]" in h else h.split(":")[0]
            return host.lower() in _ALLOWED_HOSTS

        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, code: int, msg: str) -> None:
            self._json({"error": msg}, code)

        def do_GET(self):                       # noqa: N802
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "host not allowed")
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                if u.path == "/":
                    return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                if u.path == "/api/status":
                    return self._json(ws.status(refresh="refresh" in q))
                if u.path == "/api/tasks":
                    return self._json(ws.tasks())
                if u.path.startswith("/api/tasks/"):
                    return self._json(ws.task(u.path.rsplit("/", 1)[1]))
                if u.path == "/api/inspections":
                    return self._json(ws.inspections())
                if u.path == "/api/inspection":
                    k = q.get("key", [""])[0]
                    return self._json({**ws.inspection(k), **ws.review_state(k)})
                if u.path == "/api/projects":
                    return self._json(ws.projects_list())
                if u.path == "/api/project":
                    return self._json(ws.project(q.get("id", [""])[0]))
                if u.path == "/api/changes":
                    return self._json(ws.changes())
                if u.path == "/file":
                    p = ws.resolve_key(q.get("key", [""])[0])
                    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    disp = "inline" if ctype.startswith("image/") or ctype == "application/pdf" \
                        else "attachment"
                    return self._send(200, p.read_bytes(), ctype,
                                      {"Content-Disposition": f'{disp}; filename="{p.name}"'})
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            except PermissionError as ex:
                return self._error(HTTPStatus.FORBIDDEN, str(ex))
            except KeyError as ex:
                return self._error(HTTPStatus.NOT_FOUND, f"unknown: {ex}")
            except Exception as ex:              # report, never crash the server
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(ex).__name__}: {ex}")

        def _drain(self, n: int, cap: int) -> None:
            left = n if n <= cap else 0
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                left -= len(chunk)

        def _upload(self, n: int):
            """Raw request body = the file (no multipart parsing). Checks first, and every refusal
            drains the body before answering (see do_POST)."""
            from astracad_core.projects import MAX_SOURCE_BYTES, SourceRejected
            q = parse_qs(urlparse(self.path).query)
            refuse = None
            if not self._host_ok():
                refuse = (HTTPStatus.FORBIDDEN, "host not allowed")
            elif self.headers.get("X-AstraCAD") != "1":
                refuse = (HTTPStatus.FORBIDDEN, "missing X-AstraCAD header")
            elif n <= 0:
                refuse = (HTTPStatus.LENGTH_REQUIRED, "Content-Length required")
            elif n > MAX_SOURCE_BYTES:
                refuse = (HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                          f"file larger than {MAX_SOURCE_BYTES // 2**20} MiB")
            if refuse:
                self._drain(n, MAX_SOURCE_BYTES)
                self.close_connection = True
                return self._error(*refuse)
            class _Counted:                     # how much of the body the store consumed
                def __init__(self, f):
                    self.f, self.n = f, 0

                def read(self, k):
                    b = self.f.read(k)
                    self.n += len(b)
                    return b

            body = _Counted(self.rfile)
            try:
                rec = ws.upload(q.get("project", [""])[0], q.get("name", [""])[0], body, n,
                                q.get("by", [""])[0])
                return self._json(rec, 201)
            except (SourceRejected, PermissionError, FileNotFoundError, ValueError) as ex:
                self._drain(n - body.n, MAX_SOURCE_BYTES)    # whatever the store did not read
                self.close_connection = True
                code = (HTTPStatus.UNSUPPORTED_MEDIA_TYPE if isinstance(ex, SourceRejected) else
                        HTTPStatus.FORBIDDEN if isinstance(ex, PermissionError) else
                        HTTPStatus.NOT_FOUND if isinstance(ex, FileNotFoundError) else
                        HTTPStatus.BAD_REQUEST)
                return self._error(code, str(ex))

        def do_POST(self):                      # noqa: N802
            # Read the request body BEFORE any reply: answering 403 with the client still sending
            # made Windows reset the connection (intermittent ConnectionAborted in tests).
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if urlparse(self.path).path == "/api/upload":
                return self._upload(n)
            if n > (1 << 16):
                # discard (bounded) what the client is sending, then refuse: replying mid-upload
                # made Windows abort the connection before the 413 could be read
                left = n if n <= (8 << 20) else 0
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 16))
                    if not chunk:
                        break
                    left -= len(chunk)
                self.close_connection = True
                return self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body too large")
            raw = self.rfile.read(n) if n > 0 else b""
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "host not allowed")
            if self.headers.get("X-AstraCAD") != "1":
                return self._error(HTTPStatus.FORBIDDEN, "missing X-AstraCAD header")
            u = urlparse(self.path)
            try:
                body = json.loads(raw or b"{}")
                if u.path == "/api/projects":
                    return self._json(ws.create_project(body))
                if u.path == "/api/inspect" and body.get("source"):
                    return self._json(ws.inspect_source(body))
                if u.path == "/api/inspect":
                    return self._json(ws.run_inspection(str(body.get("drawing", "")),
                                                        body.get("units"), body.get("revision"),
                                                        body.get("general_tolerance")))
                if u.path == "/api/review":
                    return self._json(ws.review(str(body.get("key", "")), body))
                if u.path == "/api/reissue":
                    return self._json(ws.reissue(str(body.get("key", ""))))
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            except PermissionError as ex:
                return self._error(HTTPStatus.FORBIDDEN, str(ex))
            except (FileNotFoundError, ValueError) as ex:
                return self._error(HTTPStatus.BAD_REQUEST, f"{type(ex).__name__}: {ex}")
            except Exception as ex:
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(ex).__name__}: {ex}")

    return Handler


def serve(ws: Workspace, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(ws))
