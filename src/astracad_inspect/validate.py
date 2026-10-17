"""Reopen what was written and check it against the plan.

Writing a file is not the same as producing a readable file. Each artefact is re-opened here
with a library other than the one that produced it, and its contents are compared against the
plan object: row counts, balloon numbers, limit values, the release-gate sentence, and whether
the drawing page actually carries ink. A PDF that renders as a blank sheet, or a workbook whose
limit column silently dropped a value, fails here instead of failing in the inspection bay.
"""
from __future__ import annotations

import json
from pathlib import Path

from .export.pdf import fmt_number
from .model import Check, CheckOutcome
from .plan import InspectionPlan


def _c(name: str, ok: bool, expected: str, actual: str, why: str | None = None) -> Check:
    return Check(name, CheckOutcome.PASSED if ok else CheckOutcome.FAILED, expected, actual,
                 None if ok else (why or name))


def _missing(needle: str, text: str) -> bool:
    return needle not in text


class _PdfView:
    """The few read-back operations the PDF checks need, behind one interface.

    Two independent engines can serve it. pypdfium2 (Apache-2.0/BSD-3, wrapping Google's PDFium)
    is preferred because it carries no copyleft obligation; PyMuPDF (AGPL-3.0 or commercial) is
    used only when pypdfium2 is absent. Neither is the library that wrote the file (reportlab), so
    the check still reads the artefact with an independent parser."""

    def __init__(self, path: Path):
        self.engine = None
        self._doc = None
        try:
            import pypdfium2 as pdfium
            self._doc = pdfium.PdfDocument(str(path))
            self.engine = "pypdfium2"
            self._pdfium = pdfium
        except ImportError:
            try:
                import pymupdf
            except ImportError:
                return
            self._doc = pymupdf.open(str(path))
            self.engine = "pymupdf"

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()

    @property
    def page_count(self) -> int:
        return len(self._doc) if self.engine == "pypdfium2" else self._doc.page_count

    def text(self) -> str:
        if self.engine == "pymupdf":
            return "\n".join(page.get_text() for page in self._doc)
        parts = []
        for i in range(len(self._doc)):
            page = self._doc[i]
            tp = page.get_textpage()
            parts.append(tp.get_text_range())
            tp.close()
            page.close()
        return "\n".join(parts).replace("\r\n", "\n").replace("\r", "\n")

    def title(self) -> str | None:
        if self.engine == "pymupdf":
            return (self._doc.metadata or {}).get("title") or None
        return (self._doc.get_metadata_dict() or {}).get("Title") or None

    def image_page_ink(self) -> float:
        """Darkest-image-page ink fraction at 72 dpi, sampled on a 6-pixel stride."""
        best = 0.0
        if self.engine == "pymupdf":
            for page in self._doc:
                if not page.get_images():
                    continue
                s = page.get_pixmap(dpi=72).samples
                idx = range(0, len(s), 18)
                best = max(best, sum(1 for i in idx if s[i] < 250) / (len(idx) or 1))
            return best
        image_type = self._pdfium.raw.FPDF_PAGEOBJ_IMAGE
        for i in range(len(self._doc)):
            page = self._doc[i]
            if not any(True for _ in page.get_objects(filter=[image_type], max_depth=2)):
                page.close()
                continue
            img = page.render(scale=1.0).to_pil().convert("L")
            data = img.tobytes()
            idx = range(0, len(data), 6)
            best = max(best, sum(1 for j in idx if data[j] < 250) / (len(idx) or 1))
            page.close()
        return best


def verify_pdf(plan: InspectionPlan, path: Path) -> list[Check]:
    out: list[Check] = []
    if not path.exists():
        return [Check("pdf_present", CheckOutcome.FAILED, "file exists", "missing",
                      "the export step did not produce a PDF")]
    doc = _PdfView(path)
    if doc.engine is None:
        return [Check("pdf_reader_available", CheckOutcome.NOT_VERIFIABLE,
                      "pypdfium2 or PyMuPDF importable", "neither is installed",
                      "the PDF was written but cannot be re-read on this machine, so its "
                      "content is unverified; install pypdfium2")]
    try:
        pages = doc.page_count
        text = doc.text()
        out.append(_c("pdf_opens", pages > 0, "at least 1 page",
                      f"{pages} pages (read with {doc.engine})"))
        out.append(_c("pdf_rows_present",
                      not any(_missing(r.cid, text) for r in plan.rows),
                      f"{len(plan.rows)} characteristic IDs",
                      f"{sum(1 for r in plan.rows if _missing(r.cid, text))} missing",
                      "characteristic IDs that never appear in the PDF text"))
        numbers = [r.balloon_no for r in plan.rows if r.balloon_no is not None]
        absent = [n for n in numbers if str(n) not in text]
        out.append(_c("pdf_balloon_numbers_present", not absent,
                      f"{len(numbers)} balloon numbers", f"{len(absent)} absent",
                      f"absent: {absent}"))
        absent_callouts = [r.cid for r in plan.rows if r.callout not in text]
        out.append(_c("pdf_callouts_verbatim", not absent_callouts,
                      f"{len(plan.rows)} callout strings recoverable from the PDF",
                      f"{len(absent_callouts)} altered or absent",
                      "a symbol the PDF font cannot draw is substituted silently, so the "
                      "diameter sign can read as a different character: "
                      + ", ".join(absent_callouts[:6])))
        value_absent = []
        for r in plan.rows:
            for v in (r.minimum, r.maximum, r.nominal):
                if v is not None and fmt_number(v) not in text:
                    value_absent.append(f"{r.cid}:{fmt_number(v)}")
        out.append(_c("pdf_limit_values_readable", not value_absent,
                      "every stated limit appears as text", f"{len(value_absent)} absent",
                      "these values are not recoverable from the PDF text layer: "
                      + ", ".join(value_absent[:8])))
        flat = " ".join(text.split())
        gate = " ".join(plan.gate_statement().split())
        out.append(_c("pdf_gate_statement_present", gate in flat,
                      "the release-gate sentence appears in the report text",
                      "found" if gate in flat else f"not found: {gate[:60]!r}",
                      "a reader could not tell from the PDF whether this plan is releasable"))
        best_ink = doc.image_page_ink()
        out.append(_c("pdf_drawing_page_not_blank", best_ink > 0.002,
                      "an image page with more than 0.2% ink",
                      f"darkest image page: {best_ink:.4%} ink",
                      "the drawing page rendered empty; the layout cannot be inspected"))
        title = doc.title()
        out.append(_c("pdf_metadata", bool(title),
                      "title set in document metadata", repr(title)))
    finally:
        doc.close()
    return out


def verify_xlsx(plan: InspectionPlan, path: Path) -> list[Check]:
    from openpyxl import load_workbook

    out: list[Check] = []
    if not path.exists():
        return [Check("xlsx_present", CheckOutcome.FAILED, "file exists", "missing",
                      "the export step did not produce a workbook")]
    wb = load_workbook(str(path), data_only=True)
    try:
        want = {"Inspection table", "Balloons", "Checks and findings", "Feature support",
                "Provenance"}
        out.append(_c("xlsx_sheets", want <= set(wb.sheetnames),
                      ", ".join(sorted(want)), ", ".join(wb.sheetnames)))
        ws = wb["Inspection table"]
        header = [c.value for c in ws[1]]
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        out.append(_c("xlsx_row_count", len(rows) == len(plan.rows),
                      f"{len(plan.rows)} rows", f"{len(rows)} rows"))

        def col(name: str) -> int:
            return header.index(name)

        by_balloon = {r.balloon_no: r for r in plan.rows if r.balloon_no is not None}
        mismatches: list[str] = []
        seen_balloons: set[int] = set()
        for row in rows:
            cid = row[col("ID")]
            want_row = next((r for r in plan.rows if r.cid == cid), None)
            if want_row is None:
                mismatches.append(f"{cid}: not in the plan")
                continue
            for header_name, value in (("Nominal", want_row.nominal),
                                       ("Min limit", want_row.minimum),
                                       ("Max limit", want_row.maximum),
                                       ("Qty", want_row.count)):
                got = row[col(header_name)]
                if value is None:
                    if got not in (None, ""):
                        mismatches.append(f"{cid}.{header_name}: plan says unstated, "
                                          f"workbook holds {got!r}")
                elif not isinstance(got, (int, float)) or abs(float(got) - value) > 1e-9:
                    mismatches.append(f"{cid}.{header_name}: {got!r} != {value!r}")
            b = row[col("Balloon")]
            if b is not None:
                seen_balloons.add(int(b))
        out.append(_c("xlsx_values_match_plan", not mismatches,
                      "every numeric cell equals the plan value",
                      f"{len(mismatches)} mismatch(es)", "; ".join(mismatches[:6])))
        out.append(_c("xlsx_balloons_match", seen_balloons == set(by_balloon),
                      f"{len(by_balloon)} balloons", f"{len(seen_balloons)} in the workbook"))
        hash_cells = sum(1 for row in wb["Provenance"].iter_rows(values_only=True)
                         for v in row if isinstance(v, str) and "sha256=" in v)
        out.append(_c("xlsx_provenance_recorded", hash_cells > 0,
                      "at least one output hash in the Provenance sheet",
                      f"{hash_cells} cell(s) carry a sha256"))
    finally:
        wb.close()
    return out


def verify_plan_json(path: Path, plan: InspectionPlan) -> list[Check]:
    out: list[Check] = []
    if not path.exists():
        return [Check("plan_json_present", CheckOutcome.FAILED, "file exists", "missing",
                      "the structured record was not written")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as ex:
        return [Check("plan_json_parses", CheckOutcome.FAILED, "valid JSON", str(ex))]
    out.append(_c("plan_json_parses", True, "valid JSON", "valid"))
    out.append(_c("plan_json_row_count", len(data.get("rows", [])) == len(plan.rows),
                  f"{len(plan.rows)} rows", f"{len(data.get('rows', []))} rows"))
    out.append(_c("plan_json_gate_agrees", data.get("approvable") == plan.approvable,
                  str(plan.approvable), str(data.get("approvable"))))
    differs = [f"{row.get('cid')}: {row.get('minimum')},{row.get('maximum')} != "
               f"{r.minimum},{r.maximum}"
               for row, r in zip(data.get("rows", []), plan.rows)
               if (row.get("minimum"), row.get("maximum")) != (r.minimum, r.maximum)]
    out.append(_c("plan_json_limits_survived_roundtrip", not differs,
                  "every limit pair identical after serialise",
                  f"{len(differs)} differ", "; ".join(differs[:5])))
    return out


def verify_artifacts(plan: InspectionPlan, pdf: Path, xlsx: Path,
                     plan_json: Path) -> list[Check]:
    from .provenance import file_record

    checks = (verify_pdf(plan, pdf) + verify_xlsx(plan, xlsx)
              + verify_plan_json(plan_json, plan))
    records = [file_record(p) for p in (pdf, xlsx, plan_json) if Path(p).exists()]
    non_empty = [r for r in records if r["bytes"] > 0]
    checks.append(_c("artifacts_non_empty", len(non_empty) == len(records) and bool(records),
                     f"{len(records)} artefacts with content",
                     f"{len(non_empty)} non-empty"))
    hashes = [r["sha256"] for r in records]
    checks.append(_c("artifact_hashes_distinct", len(set(hashes)) == len(hashes),
                     f"{len(hashes)} distinct hashes", f"{len(set(hashes))} distinct"))
    return checks
