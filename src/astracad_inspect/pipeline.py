"""End-to-end run: drawing in, deliverables out, exports read back and checked.

Order matters here. The plan is serialised before the report is written, and the provenance
manifest is written last, because a file cannot record its own hash - the manifest is what makes
the report and workbook reproducible.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .ingest.dxf import read_dxf
from .ingest.pdf import read_pdf
from .plan import InspectionPlan, apply_overrides, build_plan
from .provenance import ProvenanceRecord, build_record, stamp
from .validate import verify_artifacts


@dataclass
class RunResult:
    plan: InspectionPlan
    record: ProvenanceRecord
    artifacts: dict[str, Path] = field(default_factory=dict)
    verification: list = field(default_factory=list)

    @property
    def verification_passed(self) -> int:
        from .model import CheckOutcome
        return sum(1 for c in self.verification if c.outcome is CheckOutcome.PASSED)

    @property
    def delivered(self) -> bool:
        """A run is delivered only when the exports read back correctly. The plan's own gate is a
        separate question: an unreleasable plan can still be exported, and must be."""
        return self.verification_passed == len(self.verification) and bool(self.artifacts)


def _write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
                    encoding="utf-8")
    return path


def read_drawing(path: str | Path, declared_unit: str | None = None,
                 revision: str | None = None):
    """Pick the ingestion route from the file type: DXF (vector, with dimension entities) or
    vector PDF (text only). Anything else is refused rather than guessed at."""
    suffix = Path(path).suffix.lower()
    if suffix == ".dxf":
        return read_dxf(path, declared_unit, revision), "dxf-vector"
    if suffix == ".pdf":
        try:
            return read_pdf(path, declared_unit, revision), "pdf-vector"
        except ValueError as ex:
            if "no text layer" not in str(ex):
                raise
            # A scanned PDF: read it as an image, with two-engine OCR consensus.
            from .ingest.raster import read_raster
            return read_raster(path, declared_unit, revision), "raster-ocr"
    from .ingest.raster import RASTER_SUFFIXES
    if suffix in RASTER_SUFFIXES:
        from .ingest.raster import read_raster
        return read_raster(path, declared_unit, revision), "raster-ocr"
    raise ValueError(f"unsupported drawing type {suffix!r}: .dxf, .pdf and "
                     f"{', '.join(sorted(RASTER_SUFFIXES))} are read")


def inspect_drawing(drawing_path: str | Path, out_dir: str | Path, *,
                    declared_unit: str | None = None, revision: str | None = None,
                    overrides: str | Path | None = None,
                    balloon_radius: float = 4.0) -> RunResult:
    src = Path(drawing_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    bundle, route = read_drawing(src, declared_unit, revision)
    inputs = [src]
    if overrides:
        apply_overrides(bundle, overrides)
        inputs.append(Path(overrides))
    plan = build_plan(bundle, balloon_radius=balloon_radius)

    from .export.render import render_drawing, render_pdf_page, render_raster_page
    png = out / f"{stem}_layout.png"
    render = {"pdf-vector": render_pdf_page, "raster-ocr": render_raster_page}.get(route, render_drawing)
    render_info = render(src, plan.layout, png,
                         caption=f"{stem} rev {plan.revision} - inspection layout")

    plan_json = _write_json(out / f"{stem}.plan.json", plan.to_dict())

    record = build_record(inputs, route=route, plan=plan)
    for what in render_info["entities_not_drawn"]:
        record.limitations.append(
            f"layout preview: {what} could not be decomposed by ezdxf and is absent from the image; "
            "the extraction and the measured extents cover the entities that could be read")
    stamp(record, [png, plan_json])
    record.limitations.append(
        "the PDF and XLSX hashes are recorded in provenance.json; an artefact cannot carry its "
        "own hash, so the report does not claim one")

    from .export.pdf import write_report
    from .export.xlsx import write_workbook
    pdf = write_report(plan, record, png, out / f"{stem}_inspection_report.pdf")
    xlsx = write_workbook(plan, record, out / f"{stem}_inspection_table.xlsx")

    verification = verify_artifacts(plan, pdf, xlsx, plan_json)
    artifacts = {"png": png, "plan_json": plan_json, "pdf": pdf, "xlsx": xlsx}

    stamp(record, list(artifacts.values()))
    _write_json(out / f"{stem}.verification.json", [
        {"name": c.name, "outcome": c.outcome.value, "expected": c.expected,
         "actual": c.actual, "why": c.why, "delta": c.delta} for c in verification])
    _write_json(out / f"{stem}.provenance.json", {
        **record.to_dict(),
        "plan_gate": plan.gate_statement(),
        "plan_approvable": plan.approvable,
        "export_verification": [
            {"name": c.name, "outcome": c.outcome.value, "expected": c.expected,
             "actual": c.actual, "why": c.why} for c in verification],
        "artifacts": {k: str(v) for k, v in artifacts.items()},
    })
    return RunResult(plan=plan, record=record, artifacts=artifacts, verification=verification)
