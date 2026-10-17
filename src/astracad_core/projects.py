"""Projects and their source documents.

A project is a folder with a manifest:

    <root>/<project-id>/project.json        name, created, the source index
    <root>/<project-id>/sources/<sha256>/<original file name>
    <root>/<project-id>/runs/<run folders>

Source documents are preserved exactly as received: stored by content hash, never overwritten (a
second upload of the same bytes is recognised, not duplicated), and made read-only on disk. Every
inspection reads the preserved copy and re-checks its hash first, so a result can always be traced
to the exact bytes it came from (constitution Part 9 items 1-3, Part 18 "do not overwrite original
engineering documents silently").

Uploads are untrusted input. A file is accepted only when its extension is a supported drawing type
AND its leading bytes are that type's signature, its size is within the limit, and its name reduces
to a plain file name. Nothing here parses the drawing; that happens later in the inspection
pipeline, on the preserved copy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_SOURCE_BYTES = 100 * 1024 * 1024          # a large A0 scan is well under this
_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_SHA = re.compile(r"[0-9a-f]{64}")


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# extension -> (media type, signature test on the first bytes)
def _is_dxf(head: bytes) -> bool:
    if head.startswith(b"AutoCAD Binary DXF"):
        return True
    t = head.lstrip(b"\xef\xbb\xbf").lstrip()
    # ASCII DXF: group code 0 then SECTION, or a 999 comment group first
    return bool(re.match(rb"(0|999)\s*\r?\n", t)) and (b"SECTION" in head[:2048] or t.startswith(b"999"))


SIGNATURES = {
    ".dxf": ("image/vnd.dxf", _is_dxf),
    ".pdf": ("application/pdf", lambda h: h.lstrip()[:5] == b"%PDF-"),
    ".png": ("image/png", lambda h: h.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", lambda h: h.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda h: h.startswith(b"\xff\xd8\xff")),
    ".tif": ("image/tiff", lambda h: h[:4] in (b"II*\x00", b"MM\x00*")),
    ".tiff": ("image/tiff", lambda h: h[:4] in (b"II*\x00", b"MM\x00*")),
    ".bmp": ("image/bmp", lambda h: h.startswith(b"BM")),
}


class SourceRejected(ValueError):
    """The upload is not a supported drawing, or not what its name says it is."""


@dataclass
class SourceRecord:
    sha256: str
    filename: str
    size: int
    media_type: str
    added_utc: str
    added_by: str = ""
    uploads: int = 1            # how many times these exact bytes were received

    @property
    def relpath(self) -> str:
        return f"sources/{self.sha256}/{self.filename}"


@dataclass
class Project:
    id: str
    name: str
    created_utc: str
    description: str = ""
    sources: list[SourceRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for s, rec in zip(d["sources"], self.sources):
            s["relpath"] = rec.relpath
        return d


def safe_filename(name: str) -> str:
    """Reduce an uploaded name to a plain file name: no folders, no device names, no control or
    shell-special characters. The extension is kept (lower-cased) because it selects the reader."""
    base = re.split(r"[\\/]", str(name or ""))[-1].strip().strip(".")
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = re.sub(r"[^\w .()+-]", "_", stem, flags=re.ASCII).strip(" .")[:120] or "drawing"
    if stem.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
                        *(f"LPT{i}" for i in range(10))}:
        stem = "_" + stem
    ext = re.sub(r"[^a-z0-9]", "", ext.lower())[:8]
    return f"{stem}.{ext}" if ext else stem


class ProjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- projects -------------------------------------------------------------------------
    def _dir(self, pid: str) -> Path:
        if not _ID.fullmatch(str(pid or "")):
            raise PermissionError("invalid project id")
        d = (self.root / pid).resolve()
        if d.parent != self.root:
            raise PermissionError("project outside the project root")
        return d

    def _load(self, pid: str) -> Project:
        p = self._dir(pid) / "project.json"
        if not p.is_file():
            raise FileNotFoundError(f"no project {pid!r}")
        d = json.loads(p.read_text(encoding="utf-8"))
        return Project(d["id"], d["name"], d["created_utc"], d.get("description", ""),
                       [SourceRecord(**{k: v for k, v in s.items() if k != "relpath"})
                        for s in d.get("sources", [])])

    def _save(self, pr: Project) -> None:
        d = self._dir(pr.id)
        tmp = d / "project.json.tmp"
        tmp.write_text(json.dumps(pr.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "project.json")

    def create(self, name: str, description: str = "") -> Project:
        name = re.sub(r"\s+", " ", str(name or "")).strip()
        if not (1 <= len(name) <= 120) or any(ord(c) < 32 for c in name):
            raise ValueError("project name: 1-120 printable characters")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "project"
        if len(slug) < 3:
            slug = (slug + "-prj")[:40]
        with self._lock:
            pid = f"{slug}-{secrets.token_hex(3)}"
            d = self._dir(pid)
            d.mkdir()
            (d / "sources").mkdir()
            (d / "runs").mkdir()
            pr = Project(pid, name, _utc(), str(description or "")[:2000])
            self._save(pr)
        return pr

    def list(self) -> list[Project]:
        out = []
        for d in sorted(self.root.iterdir()):
            if d.is_dir() and _ID.fullmatch(d.name) and (d / "project.json").is_file():
                try:
                    out.append(self._load(d.name))
                except (OSError, ValueError, KeyError):
                    continue
        return sorted(out, key=lambda p: p.created_utc, reverse=True)

    def get(self, pid: str) -> Project:
        return self._load(pid)

    def runs_dir(self, pid: str) -> Path:
        self._load(pid)
        return self._dir(pid) / "runs"

    # ---- sources --------------------------------------------------------------------------
    def add_source(self, pid: str, filename: str, stream, length: int, added_by: str = "") -> SourceRecord:
        """Store an upload from `stream` (exactly `length` bytes). Validated, hashed while written
        to a temporary file in the project, then moved into place and made read-only."""
        pr = self._load(pid)
        name = safe_filename(filename)
        ext = Path(name).suffix.lower()
        if ext not in SIGNATURES:
            raise SourceRejected(f"{ext or 'no extension'}: supported drawings are "
                                 + ", ".join(sorted(SIGNATURES)))
        if not (0 < length <= MAX_SOURCE_BYTES):
            raise SourceRejected(f"size {length} bytes: must be 1 byte to {MAX_SOURCE_BYTES // 2**20} MiB")
        d = self._dir(pid)
        h = hashlib.sha256()
        fd, tmp_name = tempfile.mkstemp(prefix=".upload-", dir=d)
        tmp = Path(tmp_name)
        try:
            got, head = 0, b""
            with os.fdopen(fd, "wb") as f:
                while got < length:
                    chunk = stream.read(min(1 << 20, length - got))
                    if not chunk:
                        break
                    if len(head) < 4096:
                        head += chunk[:4096 - len(head)]
                    h.update(chunk)
                    f.write(chunk)
                    got += len(chunk)
            if got != length:
                raise SourceRejected(f"upload ended after {got} of {length} bytes")
            media, sig = SIGNATURES[ext]
            if not sig(head):
                raise SourceRejected(f"the content is not a {ext} file (its leading bytes do not match)")
            sha = h.hexdigest()
            with self._lock:
                pr = self._load(pid)
                same = next((s for s in pr.sources if s.sha256 == sha), None)
                if same is not None:
                    same.uploads += 1
                    self._save(pr)
                    return same
                dest_dir = d / "sources" / sha
                dest_dir.mkdir(parents=True, exist_ok=False)
                dest = dest_dir / name
                os.replace(tmp, dest)
                os.chmod(dest, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
                rec = SourceRecord(sha, name, got, media, _utc(), str(added_by or "")[:64])
                pr.sources.append(rec)
                self._save(pr)
                return rec
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def source_path(self, pid: str, sha: str, verify: bool = True) -> Path:
        """The preserved file, after checking it still has the bytes it was stored with."""
        if not _SHA.fullmatch(str(sha or "")):
            raise PermissionError("invalid source id")
        pr = self._load(pid)
        rec = next((s for s in pr.sources if s.sha256 == sha), None)
        if rec is None:
            raise FileNotFoundError("no such source in this project")
        p = self._dir(pid) / "sources" / sha / rec.filename
        if not p.is_file():
            raise FileNotFoundError(f"preserved source missing: {rec.relpath}")
        if verify:
            h = hashlib.sha256()
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            if h.hexdigest() != sha:
                raise ValueError(f"preserved source {rec.relpath} no longer matches its SHA-256; "
                                 "it was modified outside ASTraCAD and will not be inspected")
        return p
