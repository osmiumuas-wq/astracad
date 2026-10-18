"""Quality dossier: everything recorded about one inspection run (and one serial), in one package.

**Per-run dossier** (`build`): `<stem>_dossier[_<serial>].zip` holds the run's own records as they
are on disk - plan, provenance, export read-back, report, workbook, layout pages, measurement log,
review and import logs, imported files, checklist exports, NCRs, and the source drawing (only when
its SHA-256 still matches the plan) - plus `dossier.pdf`, a cover summary, and `manifest.json`
listing every file with its SHA-256 and size.

**Project-level dossier** (`build_project`): one ZIP per project, covering all runs, all serials,
all re-issues, all NCRs and all started checklists. The ZIP nests each run under `runs/<run-stem>/`
and includes preserved source drawings under `sources/`. The cover PDF gives a project-wide
conformance statement and per-run detail sections. Conforming only when every run is releasable,
every serial across all runs is acceptable, every NCR is closed or cancelled, and every started
checklist is signed.

After writing, both package types are re-opened and every file is hashed again against the manifest;
the PDF is opened and its pages counted.

The cover states what the records show and nothing more. It says "conforming" only when nothing is
open. A dossier is evidence, not an approval.
The PDF cannot carry its own hash (it is listed in the manifest's `self` note instead).
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import zipfile
from collections import Counter
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
    """Re-open the package: every manifest entry present with the same SHA-256; the PDF opens; any
    signatures (signatures/NN.json, outside the manifest they sign) check out."""
    from .signing import check as check_signatures, signature_files
    checks = []
    with zipfile.ZipFile(out_zip) as z:
        names = set(z.namelist())
        man = json.loads(z.read("manifest.json"))
        bad = [f["path"] for f in man["files"] if f["path"] not in names or _sha(z.read(f["path"])) != f["sha256"]]
        checks.append(("manifest_hashes", not bad, f"{len(man['files'])} files" if not bad else f"mismatch: {bad[:5]}"))
        extra = sorted(names - {f["path"] for f in man["files"]} - {"manifest.json"} - set(signature_files(z)))
        checks.append(("no_unlisted_files", not extra, "none" if not extra else ", ".join(extra[:5])))
        try:
            import pypdfium2 as pdfium
            n = len(pdfium.PdfDocument(io.BytesIO(z.read("dossier.pdf"))))
            checks.append(("pdf_opens", n >= 1, f"{n} page(s)"))
        except Exception as e:  # noqa: BLE001 - reported as a failed check
            checks.append(("pdf_opens", False, f"{type(e).__name__}: {e}"))
    sigs = check_signatures(out_zip)
    if sigs:
        broken = [s for s in sigs if not s["valid"]]
        checks.append(("signatures", not broken, f"{len(sigs)} check out" if not broken else
                       f"{len(broken)} of {len(sigs)} do not check out: {broken[0]['problems'][0]}"))
    return checks


# ---- project-level dossier ---------------------------------------------------------------


def project_status(runs: list[dict]) -> dict:
    """Aggregate conformance across all runs in a project.

    Each entry in *runs* is a dict with at least:
        plan  - the loaded plan JSON
        meas  - evaluate() result for this run
        ncrs  - list of NCR dicts relevant to this run
        checklists - list of checklist state dicts relevant to this run
        samples - list of sample IDs measured in this run (may be empty)
        stem   - run stem name
        run_id - (optional) the run's folder, unique in the project; re-inspections of one drawing
                 share a stem, so this is what names the run in reports and in the archive
        problems - (optional) records that could not be produced; each is an open item

    Returns {"conforming": bool, "open_items": [...], "run_statuses": [...]}.
    """
    open_items: list[str] = []
    run_statuses: list[dict] = []
    for r in runs:
        plan, meas = r["plan"], r["meas"]
        label = _run_label(r)
        samples = r["samples"] or [None]
        for sample in samples:
            st = status(plan, meas, sample, r["ncrs"], r["checklists"])
            run_statuses.append({
                "stem": r["stem"], "run": label, "sample": sample, **st,
            })
            for it in st["open_items"]:
                open_items.append(f"{label}" + (f" / {sample}" if sample else "") + f": {it}")
        for p in r.get("problems") or []:
            open_items.append(f"{label}: {p}")
    return {"conforming": not open_items, "open_items": open_items, "run_statuses": run_statuses}


def _run_label(r: dict) -> str:
    return str(r.get("run_id") or r["stem"])


def _project_pdf(path: Path, project_name: str, runs: list[dict], ncrs: list[dict],
                 manifest: list[dict], st: dict, sources: list[dict]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from .export.pdf import _esc, _styles, _table
    S = _styles()
    title = f"Project quality dossier - {project_name}"
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm, title=title, author="ASTraCAD", creator="astracad_inspect.dossier")
    W = 273 * mm
    story = [Paragraph(_esc(title), S["h1"])]
    ok = st["conforming"]
    color = "#0a7d33" if ok else "#b00020"
    head = ("<b>CONFORMING ON THE RECORDS</b> - every run releasable, every serial complete without failure, "
            "no open NCR, every started checklist signed."
            if ok else "<b>NOT CONFORMING / NOT COMPLETE</b> - open items:")
    story.append(Paragraph(f"<font color='{color}'>{head}</font>", S["gate"]))
    for it in st["open_items"]:
        story.append(Paragraph("&bull; " + _esc(it), S["body"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(_esc(f"Project: {project_name}  |  {len(runs)} run(s)  |  "
                                f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}. "
                                "A dossier is a record of what was done, not an approval; acceptance is the organisation's decision."), S["small"]))

    # ---- summary table of runs ----
    story += [Spacer(1, 3 * mm), Paragraph("Inspection runs", S["h2"])]
    data = [["Run", "Drawing", "Revision", "Rows", "Releasable", "Serials measured"]]
    for r in runs:
        plan = r["plan"]
        tb = plan.get("title_block") or {}
        drawing = tb.get("drawing_number") or tb.get("part_name") or Path(plan.get("source_document") or "").stem
        data.append([Paragraph(_esc(_run_label(r)), S["cell"]), Paragraph(_esc(drawing), S["cell"]), plan.get("revision", ""),
                     len(plan.get("rows") or []),
                     "yes" if plan.get("approvable") else "NO",
                     ", ".join(r["samples"]) or "-"])
    story.append(_table(data, [50 * mm, 60 * mm, 18 * mm, 15 * mm, 24 * mm, 106 * mm]))

    # ---- per-run detail sections ----
    for r in runs:
        plan, meas = r["plan"], r["meas"]
        tb = plan.get("title_block") or {}
        part = tb.get("drawing_number") or tb.get("part_name") or Path(plan.get("source_document") or "").stem
        story += [Spacer(1, 4 * mm), Paragraph(f"Run: {_esc(_run_label(r))} — {_esc(part)} rev {plan.get('revision')}", S["h2"])]

        for sample in (r["samples"] or [None]):
            if sample:
                story.append(Paragraph(f"Serial {_esc(sample)}", S["h2"]))
            rows = plan.get("rows") or []
            data_rows = [["#", "Requirement", "Limits", "Value", "Verdict"]]
            for row in rows:
                c = (meas.get("cells") or {}).get(f"{sample}|{row['cid']}") if sample else None
                lim = f"{row['minimum']:g} .. {row['maximum']:g}" if row.get("minimum") is not None and row.get("maximum") is not None else "-"
                data_rows.append([row.get("balloon_no"),
                                  Paragraph(_esc(row.get("callout") or ""), S["cell"]), lim,
                                  "-" if not c or c.get("value") is None else f"{c['value']:g}",
                                  c["verdict"] if c else "not measured"])
            t = _table(data_rows, [10 * mm, 85 * mm, 30 * mm, 22 * mm, 30 * mm])
            for i, dr in enumerate(data_rows[1:], start=1):
                if dr[4] == "FAIL":
                    t.setStyle(TableStyle([("TEXTCOLOR", (4, i), (4, i), colors.HexColor("#b00020"))]))
            story.append(t)

    # ---- NCRs ----
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

    # ---- sources ----
    if sources:
        story += [Spacer(1, 3 * mm), Paragraph("Preserved source drawings", S["h2"])]
        data = [["File", "SHA-256", "Media type", "Size"]]
        for s in sources:
            data.append([s["filename"], s["sha256"][:16] + "…", s["media_type"], f"{s['size']:,}"])
        story.append(_table(data, [70 * mm, 50 * mm, 40 * mm, 30 * mm]))

    # ---- contents table ----
    story += [Spacer(1, 3 * mm), Paragraph("Contents (SHA-256; manifest.json in the package lists the same)", S["h2"])]
    data = [["File", "Bytes", "SHA-256"]] + [[Paragraph(_esc(m["path"]), S["cell"]), m["bytes"], m["sha256"]] for m in manifest]
    story.append(_table(data, [120 * mm, 22 * mm, 131 * mm]))
    doc.build(story)


def build_project(project_name: str, out_zip: Path, *,
                  runs: list[dict], ncrs: list[dict], sources: list[dict]) -> dict:
    """Build a project-level quality dossier ZIP.

    *runs*: list of dicts, each with keys:
        plan_path  - Path to the .plan.json
        plan       - loaded plan JSON
        meas       - evaluate() result
        ncrs       - NCRs for this run
        checklists - checklist states for this run
        samples    - sample IDs
        stem       - run stem name
        run_files  - list of (arc_name, bytes) for this run's files in the ZIP

    *ncrs*: all NCRs across the project (deduplicated).
    *sources*: list of dicts with filename, sha256, size, media_type, path (Path to preserved file).

    Returns {"zip": Path, "manifest": dict, "status": dict}.
    """
    files: list[tuple[str, bytes]] = []

    # per-run files, nested under runs/<run folder>/ (a re-inspection of the same drawing has the
    # same stem in another folder: keyed by stem, three runs wrote one name three times)
    for r in runs:
        plan_path = r["plan_path"]
        stem = r["stem"]
        rid = _run_label(r)
        folder = plan_path.parent
        for p in sorted(folder.iterdir()):
            if p.is_file() and p.name.startswith(stem) and "_dossier" not in p.name and not p.name.endswith(".tmp"):
                files.append((f"runs/{rid}/{p.name}", p.read_bytes()))
        if (folder / "imports").is_dir():
            for p in sorted((folder / "imports").iterdir()):
                files.append((f"runs/{rid}/imports/{p.name}", p.read_bytes()))

    # NCRs
    seen_ncrs: set[str] = set()
    for n in ncrs:
        if n["number"] not in seen_ncrs:
            seen_ncrs.add(n["number"])
            files.append((f"ncr/{n['number']}.json", json.dumps(n, indent=1, ensure_ascii=False).encode("utf-8")))

    # preserved source drawings
    source_notes: list[str] = []
    for s in sources:
        src_path = s.get("path")
        if src_path and Path(src_path).is_file():
            b = Path(src_path).read_bytes()
            if _sha(b) == s["sha256"]:
                # by hash, as the project store keeps them: two revisions may share a file name
                files.append((f"sources/{s['sha256']}/{Path(s['filename']).name}", b))
                source_notes.append(f"{s['filename']}: included (SHA-256 verified)")
            else:
                source_notes.append(f"{s['filename']}: NOT included (SHA-256 mismatch)")
        else:
            source_notes.append(f"{s['filename']}: NOT included (file not found)")

    names = Counter(a for a, _ in files)
    clash = sorted(a for a, k in names.items() if k > 1)
    if clash:
        raise ValueError(f"two records would share the archive name {clash[0]!r}; nothing was written")
    manifest = [{"path": a, "bytes": len(b), "sha256": _sha(b)} for a, b in files]
    st = project_status(runs)

    pdf_buf = Path(str(out_zip) + ".cover.pdf")
    _project_pdf(pdf_buf, project_name, runs, ncrs, manifest, st, sources)
    pdf_bytes = pdf_buf.read_bytes()
    pdf_buf.unlink()

    man = {
        "generator": "astracad_inspect.dossier",
        "type": "project",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": project_name,
        "runs": [_run_label(r) for r in runs],
        "status": st,
        "sources": source_notes,
        "files": manifest + [{"path": "dossier.pdf", "bytes": len(pdf_bytes), "sha256": _sha(pdf_bytes)}],
        "self": "manifest.json cannot list its own hash; dossier.pdf's own contents table excludes dossier.pdf",
    }
    out_zip = Path(out_zip)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for a, b in files:
            z.writestr(a, b)
        z.writestr("dossier.pdf", pdf_bytes)
        z.writestr("manifest.json", json.dumps(man, indent=1, ensure_ascii=False))
    return {"zip": out_zip, "manifest": man, "status": st}
