"""Provenance: what produced each artefact, from what input, with which library versions.

A report that cannot say which build produced it cannot be re-created, and an engineering
record that cannot be re-created is not evidence. Source hashes are recorded so a later run can
tell whether it was pointed at the same drawing or a revised one.
"""
from __future__ import annotations

import hashlib
import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ENGINE = "astracad-inspect"
TOOL_PACKAGES = {
    # display label: (module to import, distribution that owns it) - the two differ for Pillow
    "ezdxf": ("ezdxf", "ezdxf"),
    "pypdfium2": ("pypdfium2", "pypdfium2"),
    "PyMuPDF": ("pymupdf", "pymupdf"),
    "matplotlib": ("matplotlib", "matplotlib"),
    "reportlab": ("reportlab", "reportlab"),
    "openpyxl": ("openpyxl", "openpyxl"),
    "Pillow": ("PIL", "pillow"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_record(path: str | Path) -> dict:
    p = Path(path)
    return {"path": str(p), "bytes": p.stat().st_size, "sha256": sha256_file(p),
            "modified_utc": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
            .isoformat(timespec="seconds")}


def tool_versions() -> dict[str, str]:
    """Versions actually importable in this process, not versions declared as wanted."""
    out: dict[str, str] = {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
    }
    for label, (module, dist) in TOOL_PACKAGES.items():
        try:
            __import__(module)
            out[label] = metadata.version(dist)
        except Exception as ex:  # a missing optional route is recorded, not hidden
            out[label] = f"UNAVAILABLE ({type(ex).__name__})"
    return out


@dataclass
class ProvenanceRecord:
    created_utc: str
    engine: str
    engine_version: str
    tools: dict[str, str]
    inputs: list[dict]
    outputs: list[dict] = field(default_factory=list)
    route: str = ""
    limitations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def build_record(inputs: list[str | Path], *, route: str, plan=None,
                 extra_limitations: list[str] | None = None) -> ProvenanceRecord:
    from . import __version__

    limitations: list[str] = []
    if plan is not None:
        unsupported = [f"{feature}: {state}"
                       for feature, state in plan.feature_support.items()
                       if state.startswith("NOT") or state.startswith("partial")]
        limitations.extend(unsupported)
        limitations.extend(f"warning: {w}" for w in plan.warnings)
    limitations.extend(extra_limitations or [])
    if plan is not None:
        limitations.append(
            f"coverage: {plan.coverage.characteristics_extracted} of "
            f"{plan.coverage.dimensions_in_source} dimension entities produced a characteristic "
            f"({plan.coverage.extraction_rate_text}); {plan.coverage.annotations_escalated} "
            "annotation text(s) escalated for review")

    return ProvenanceRecord(
        created_utc=_now(),
        engine=ENGINE,
        engine_version=__version__,
        tools=tool_versions(),
        inputs=[file_record(p) for p in inputs],
        route=route,
        limitations=limitations,
    )


def stamp(record: ProvenanceRecord, outputs: list[str | Path]) -> ProvenanceRecord:
    """Record the produced artefacts. Call after writing, so the hash is the file's real one."""
    record.outputs = [file_record(p) for p in outputs]
    return record
