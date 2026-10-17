"""Quality dossier: everything recorded about one inspection run (and one serial), in one package.

`<stem>_dossier[_<serial>].zip` holds the run's own records as they are on disk - plan, provenance,
export read-back, report, workbook, layout pages, measurement log, review and import logs, imported
files, checklist exports, NCRs, and the source drawing (only when its SHA-256 still matches the
plan) - plus `dossier.pdf`, a cover summary, and `manifest.json` listing every file with its
SHA-256 and size. After writing, the package is re-opened and every file is hashed again against
the manifest; the PDF is opened and its pages counted.

The cover states what the records show and nothing more. It says "conforming" only when the plan
is releasable, the serial is complete with no failure, no NCR is open and every checklist that was
started is signed; otherwise it lists what is still open. A dossier is evidence, not an approval.
The PDF cannot carry its own hash (it is listed in the manifest's `self` note instead).
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from pathlib import Path


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def status(plan: dict, meas: dict, sample: str | None, ncrs: list[dict], checklists: list[dict]) -> dict:
    import re as _re
    open_items = []
    bal = {r["cid"]: r.get("balloon_no") for r in plan.get("rows") or []}
    nb = lambda cids: ", ".join(f"balloon {bal.get(c, c)}" for c in cids)
    if not plan.get("approvable"):
        open_items.append("plan not releasable: " + _re.sub(r"^(NOT )?releasable\s*[—-]\s*", "", str(plan.get("gate") or ""), flags=_re.I))
    st = (meas.get("sample_state") or {}).get(sample) if sample else None
    if sample:
        if st is None:
            open_items.append(f"no measurements recorded for {sample}")
        elif st["state"] != "ACCEPTABLE":
            det = []
            if st["fails"]:
                det.append(f"failed {nb(st['fails'])}")
            if st["missing"]:
                det.append(f"{len(st['missing'])} not measured")
            if st["not_evaluable"]:
                det.append(f"{len(st['not_evaluable'])} without limits")
            open_items.append(f"serial {sample}: {st['state'].lower().replace('_', ' ')}" + (f" ({'; '.join(det)})" if det else ""))
    for n in ncrs:
        if n["status"] not in ("CLOSED", "CANCELLED"):
            open_items.append(f"{n['number']} is {n['status'].lower()}")
    for c in checklists:
        if not c.get("signed"):
            open_items.append(f"checklist '{c['title']}' not signed ({c['decided']}/{c['total']} decided)")
    return {"conforming": not open_items, "open_items": open_items, "sample_state": st}


def _pdf(path: Path, plan: dict, prov: dict, sample: str | None, meas: dict, ncrs: list[dict],
         checklists: list[dict], instruments: list[dict], manifest: list[dict], st: dict, drawing_note: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from .export.pdf import _esc, _styles, _table
    S = _styles()
    tb = plan.get("title_block") or {}
    part = tb.get("drawing_number") or tb.get("part_name") or Path(plan.get("source_document") or "").stem
    title = f"Quality dossier - {part} rev {plan.get('revision')}" + (f" - serial {sample}" if sample else "")
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title=title, author="ASTraCAD", creator="astracad_inspect.dossier")
    W = 273 * mm
    story = [Paragraph(_esc(title), S["h1"])]
    if "synthetic" in Path(plan.get("source_document") or "").name.lower():
        story.append(Paragraph("<font color='#b00020'><b>SYNTHETIC TEST FIXTURE</b> - not a real part and not a "
                               "production deliverable.</font>", S["gate"]))
    ok = st["conforming"]
    color = "#0a7d33" if ok else "#b00020"
    head = ("<b>CONFORMING ON THE RECORDS</b> - plan releasable, serial complete without failure, no open NCR, every started checklist signed."
            if ok else "<b>NOT CONFORMING / NOT COMPLETE</b> - open items:")
    story.append(Paragraph(f"<font color='{color}'>{head}</font>", S["gate"]))
    for it in st["open_items"]:
        story.append(Paragraph("&bull; " + _esc(it), S["body"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(_esc(f"Source {plan.get('source_document')}  sha256 {plan.get('source_sha256')}  | {drawing_note}"), S["small"]))
    story.append(Paragraph(_esc(f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}. A dossier is a record of what was "
                                "done, not an approval; acceptance is the organisation's decision."), S["small"]))

    rows = plan.get("rows") or []
    if sample:
        story += [Spacer(1, 3 * mm), Paragraph(f"Results - serial {_esc(sample)}", S["h2"])]
        data = [["#", "Requirement", "Limits", "Value", "Verdict", "Inspector", "Instrument (cal ref, due)", "Source"]]
        for r in rows:
            c = (meas.get("cells") or {}).get(f"{sample}|{r['cid']}")
            lim = f"{r['minimum']:g} .. {r['maximum']:g}" if r.get("minimum") is not None and r.get("maximum") is not None else "-"
            inst = ""
            if c and c.get("instrument"):
                ir = c.get("instrument_record") or {}
                inst = c["instrument"] + (f" ({ir.get('calibration_ref') or '-'}, {ir.get('calibration_due')})" if ir else " (unregistered)")
            src = (c.get("source") or {}).get("file", "typed") if c else ""
            data.append([r.get("balloon_no"), Paragraph(_esc(r.get("callout") or ""), S["cell"]), lim,
                         "-" if not c or c.get("value") is None else f"{c['value']:g}", c["verdict"] if c else "not measured",
                         c.get("inspector", "") if c else "", Paragraph(_esc(inst), S["cell"]), Paragraph(_esc(src), S["cell"])])
        t = _table(data, [10 * mm, 55 * mm, 30 * mm, 22 * mm, 26 * mm, 28 * mm, 62 * mm, 40 * mm])
        for i, r in enumerate(data[1:], start=1):
            if r[4] == "FAIL":
                t.setStyle(TableStyle([("TEXTCOLOR", (4, i), (4, i), colors.HexColor("#b00020"))]))
        story.append(t)
    if ncrs:
        story += [Spacer(1, 3 * mm), Paragraph("Nonconformance reports", S["h2"])]
        data = [["NCR", "Status", "About", "Disposition", "Closure"]]
        for n in ncrs:
            l = n.get("link") or {}
            about = f"balloon {l.get('balloon_no')} {l.get('requirement') or ''} actual {l.get('actual')}" if l.get("cid") else f"checklist {l.get('checklist')} / {l.get('item')}"
            d = n.get("disposition") or {}
            data.append([n["number"], n["status"], Paragraph(_esc(about), S["cell"]),
                         Paragraph(_esc(f"{d.get('disposition', '')} {d.get('justification', '')}" + (f" (approved {d['approved_by']})" if d.get("approved_by") else "")), S["cell"]),
                         Paragraph(_esc((n.get("closure") or {}).get("corrective_action", "")), S["cell"])])
        story.append(_table(data, [30 * mm, 26 * mm, 70 * mm, 80 * mm, 67 * mm]))
    if checklists:
        story += [Spacer(1, 3 * mm), Paragraph("Checklists", S["h2"])]
        data = [["Checklist", "Version", "Decided", "Failed", "Signed"]]
        for c in checklists:
            data.append([Paragraph(_esc(c["title"]), S["cell"]), "built-in" if c.get("builtin") else f"v{c.get('version')}",
                         f"{c['decided']}/{c['total']}", ", ".join(c.get("failed") or []) or "-",
                         f"{c['signed']['by']} {c['signed']['at']}" if c.get("signed") else "not signed"])
        story.append(_table(data, [110 * mm, 22 * mm, 22 * mm, 60 * mm, 59 * mm]))
    if instruments:
        story += [Spacer(1, 3 * mm), Paragraph("Instruments used", S["h2"])]
        data = [["Instrument", "Kind", "Resolution", "Calibration due", "Certificate", "Values"]]
        for i in instruments:
            data.append([i["id"], i.get("kind", ""), f"{i.get('resolution', '')}", i.get("calibration_due", ""), i.get("calibration_ref", ""), i["count"]])
        story.append(_table(data, [40 * mm, 35 * mm, 30 * mm, 40 * mm, 60 * mm, 25 * mm]))
    lim = prov.get("limitations") or []
    if lim:
        story += [Spacer(1, 3 * mm), Paragraph("Stated limitations of the inspection run", S["h2"])]
        story += [Paragraph("&bull; " + _esc(x), S["small"]) for x in lim[:30]]
    story += [Spacer(1, 3 * mm), Paragraph("Contents (SHA-256; manifest.json in the package lists the same)", S["h2"])]
    data = [["File", "Bytes", "SHA-256"]] + [[Paragraph(_esc(m["path"]), S["cell"]), m["bytes"], m["sha256"]] for m in manifest]
    story.append(_table(data, [120 * mm, 22 * mm, 131 * mm]))
    doc.build(story)


def build(plan_path: Path, out_zip: Path, *, sample: str | None, meas: dict, ncrs: list[dict], checklists: list[dict],
          extra: list[tuple[str, Path]], instruments: list[dict]) -> dict:
    stem = plan_path.name[:-len(".plan.json")]
    folder = plan_path.parent
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    prov_p = folder / f"{stem}.provenance.json"
    prov = json.loads(prov_p.read_text(encoding="utf-8")) if prov_p.exists() else {}
    files: list[tuple[str, bytes]] = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.name.startswith(stem) and "_dossier" not in p.name and not p.name.endswith(".tmp"):
            files.append((f"run/{p.name}", p.read_bytes()))
    if (folder / "imports").is_dir():
        for p in sorted((folder / "imports").iterdir()):
            files.append((f"run/imports/{p.name}", p.read_bytes()))
    for arc, p in extra:
        if Path(p).exists() and arc not in {a for a, _ in files}:
            files.append((arc, Path(p).read_bytes()))
    for n in ncrs:
        files.append((f"ncr/{n['number']}.json", json.dumps(n, indent=1, ensure_ascii=False).encode("utf-8")))
    # the source drawing, only if it is still the exact file the plan was made from
    meta_p = folder / f"{stem}.run.json"
    drawing_note = "source drawing not included: not found at its recorded path"
    src = Path(json.loads(meta_p.read_text(encoding="utf-8")).get("drawing") or "") if meta_p.exists() \
        else Path(plan.get("source_document") or "")
    if str(src):
        if src.is_file():
            b = src.read_bytes()
            if _sha(b) == plan.get("source_sha256"):
                files.append((f"source/{src.name}", b))
                drawing_note = "source drawing included (SHA-256 verified)"
            else:
                drawing_note = "source drawing NOT included: the file at its path no longer matches the plan's SHA-256"
        else:
            drawing_note = "source drawing not included: not found at its recorded path"
    manifest = [{"path": a, "bytes": len(b), "sha256": _sha(b)} for a, b in files]
    st = status(plan, meas, sample, ncrs, checklists)
    used: dict[str, dict] = {}
    for c in (meas.get("cells") or {}).values():
        if sample and c.get("sample") != sample:
            continue
        ir = c.get("instrument_record")
        if ir:
            u = used.setdefault(ir["id"], {**ir, "count": 0})
            u["count"] += 1
    pdf_buf = Path(str(out_zip) + ".cover.pdf")
    _pdf(pdf_buf, plan, prov, sample, meas, ncrs, checklists, list(used.values()) or instruments, manifest, st, drawing_note)
    pdf_bytes = pdf_buf.read_bytes()
    pdf_buf.unlink()
    man = {"generator": "astracad_inspect.dossier", "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "plan": f"run/{plan_path.name}", "sample": sample, "status": st, "drawing": drawing_note,
           "files": manifest + [{"path": "dossier.pdf", "bytes": len(pdf_bytes), "sha256": _sha(pdf_bytes)}],
           "self": "manifest.json cannot list its own hash; dossier.pdf's own contents table excludes dossier.pdf"}
    out_zip = Path(out_zip)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for a, b in files:
            z.writestr(a, b)
        z.writestr("dossier.pdf", pdf_bytes)
        z.writestr("manifest.json", json.dumps(man, indent=1, ensure_ascii=False))
    return {"zip": out_zip, "manifest": man, "status": st}


def verify(out_zip: Path) -> list[tuple[str, bool, str]]:
    """Re-open the package: every manifest entry present with the same SHA-256; the PDF opens."""
    checks = []
    with zipfile.ZipFile(out_zip) as z:
        names = set(z.namelist())
        man = json.loads(z.read("manifest.json"))
        bad = [f["path"] for f in man["files"] if f["path"] not in names or _sha(z.read(f["path"])) != f["sha256"]]
        checks.append(("manifest_hashes", not bad, f"{len(man['files'])} files" if not bad else f"mismatch: {bad[:5]}"))
        extra = sorted(names - {f["path"] for f in man["files"]} - {"manifest.json"})
        checks.append(("no_unlisted_files", not extra, "none" if not extra else ", ".join(extra[:5])))
        try:
            import pypdfium2 as pdfium
            n = len(pdfium.PdfDocument(io.BytesIO(z.read("dossier.pdf"))))
            checks.append(("pdf_opens", n >= 1, f"{n} page(s)"))
        except Exception as e:  # noqa: BLE001 - reported as a failed check
            checks.append(("pdf_opens", False, f"{type(e).__name__}: {e}"))
    return checks
