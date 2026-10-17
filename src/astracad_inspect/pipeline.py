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


def apply_general_tolerance(bundle, declared: str | None = None) -> dict:
    """The user's declaration wins; otherwise a statement found in the drawing's own text; with
    neither, nothing is applied and untoleranced rows keep no limits."""
    from .general_tol import apply, detect, parse_user
    if declared:
        spec = parse_user(declared)
    else:
        texts = list(bundle.notes) + [u.text for u in bundle.uninterpreted] + \
            [str(v) for v in (bundle.title_block or {}).values()]
        spec = detect(texts)
    counts = apply(bundle.characteristics, spec, bundle.unit)
    if not spec.empty:
        bundle.warnings.append(
            f"general tolerance {('ISO 2768-' + spec.iso_class) if spec.iso_class else ''}"
            f"{' places ' + str(spec.places) if spec.places else ''} ({spec.source}: "
            f"{spec.evidence or 'declared'}) applied to {counts['applied']} untoleranced row(s), "
            f"not applicable to {counts['refused']}")
    return counts


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


UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8, "um": 0.001}
MAX_SHEET_PAGES = 12


def inspect_drawing(drawing_path: str | Path, out_dir: str | Path, *,
                    declared_unit: str | None = None, revision: str | None = None,
                    overrides: str | Path | None = None,
                    general_tolerance: str | None = None,
                    balloon_radius: float = 4.0) -> RunResult:
    src = Path(drawing_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = src.stem

    bundle, route = read_drawing(src, declared_unit, revision)
    inputs = [src]
    apply_general_tolerance(bundle, general_tolerance)
    if overrides:
        apply_overrides(bundle, overrides)
        inputs.append(Path(overrides))
    # Balloon size is a printed size (4 mm radius) whatever unit the DXF is drawn in; a paged
    # source (PDF, scan) is already in page millimetres.
    to_units = 1.0 if route != "dxf-vector" else 1.0 / UNIT_MM.get(bundle.unit, 1.0)
    r_units = balloon_radius * to_units

    # 1) the sheet, page by page, BEFORE any balloon: its ink is what the layout keeps clear of
    from .balloons import Occupancy
    from .export.render import render_layout, render_sheet, row_tone
    from .plan import sheet_frames
    frames = sheet_frames(bundle, margin=14.0 * to_units)
    sheets: list[dict] = []
    occupancy: dict = {}
    sheet_errors: list[str] = []
    for pno in sorted(frames)[:MAX_SHEET_PAGES]:
        sp = out / (f"{stem}_sheet.png" if pno == 1 else f"{stem}_sheet_p{pno}.png")
        try:
            rec = render_sheet(src, route, frames[pno], sp, page=pno)
            sheets.append({"image": sp.name, **rec})
            occupancy[pno] = Occupancy.from_image(sp, rec["extents"], cell_mm=0.35 * to_units)
        except Exception as e:  # noqa: BLE001 - that page falls back to ring placement; named below
            sheet_errors.append(f"page {pno}: {type(e).__name__}: {e}")
    if len(frames) > MAX_SHEET_PAGES:
        sheet_errors.append(f"pages {MAX_SHEET_PAGES + 1}-{len(frames)} not rendered "
                            f"(limit {MAX_SHEET_PAGES}); their balloons use ring placement")

    # 2) balloons placed on clear paper next to their callouts
    plan = build_plan(bundle, balloon_radius=r_units, gap=2.0 * to_units, margin=14.0 * to_units,
                      frames=frames, occupancy=occupancy or None)
    plan_dict = plan.to_dict()
    plan_json = _write_json(out / f"{stem}.plan.json", plan_dict)
    if sheets:
        _write_json(out / f"{stem}.sheet.json", {**{k: sheets[0][k] for k in (
            "image", "extents", "width_px", "height_px")}, "pages": sheets})

    # 3) the layout pages: the same sheet with the balloons drawn over it
    tones = {r["cid"]: row_tone(r) for r in plan_dict["rows"]}
    layout_pngs: list[Path] = []
    for sh in sheets:
        pno = sh["page"]
        lp = out / (f"{stem}_layout.png" if pno == 1 else f"{stem}_layout_p{pno}.png")
        render_layout(out / sh["image"], sh["extents"],
                      [b for b in plan.layout.balloons if b.page == pno], tones, lp,
                      title=f"{stem}  -  inspection layout",
                      subtitle=f"rev {plan.revision}   |   {len(plan.layout.balloons)} balloons   |   "
                               f"units {plan.unit}", page=pno, pages=len(sheets))
        layout_pngs.append(lp)
    png = out / f"{stem}_layout.png"
    not_drawn = [w for sh in sheets for w in sh.get("entities_not_drawn", [])]
    if not layout_pngs:
        # no sheet could be rendered: the previous renderer, which draws the source directly
        from .export.render import render_drawing, render_pdf_page, render_raster_page
        render = {"pdf-vector": render_pdf_page, "raster-ocr": render_raster_page}.get(route, render_drawing)
        not_drawn = render(src, plan.layout, png,
                           caption=f"{stem} rev {plan.revision} - inspection layout")["entities_not_drawn"]
        layout_pngs = [png]

    record = build_record(inputs, route=route, plan=plan)
    for what in not_drawn:
        record.limitations.append(
            f"layout preview: {what} could not be decomposed by ezdxf and is absent from the image; "
            "the extraction and the measured extents cover the entities that could be read")
    for e in sheet_errors:
        record.limitations.append(f"sheet image not produced for {e}; the layout falls back to ring "
                                  "placement there")
    stamp(record, [*layout_pngs, plan_json])
    record.limitations.append(
        "the PDF and XLSX hashes are recorded in provenance.json; an artefact cannot carry its "
        "own hash, so the report does not claim one")

    from .export.pdf import write_report
    from .export.xlsx import write_workbook
    pdf = write_report(plan, record, layout_pngs, out / f"{stem}_inspection_report.pdf")
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
