"""Import measured results (CMM, gauge, spreadsheet exports) into a run's measurement log.

Two table shapes are read, from CSV or the first sheet of an XLSX:

* LONG - one value per row, with columns named (case-insensitive, first match wins)
      sample:      sample, serial, serial number, sn, s/n, part, part id
      balloon:     balloon, balloon no, char, characteristic, feature, cid, item, no, id
      value:       value, actual, measured, measurement, meas
      optional:    instrument / gauge / gage / equipment ; unit / units ; note / comment / remarks ;
                   date / time / measured at / timestamp
* WIDE  - first column names the balloon, every further column header is a sample (serial) and its
          cells are that sample's values.

Balloon references accepted: a number (balloon number), "CH-007" (characteristic id), or "B7",
"#7", "Balloon 7", "BAL 7". Anything else is refused - feature names a CMM program invented are not
guessed at; rename them in the export, or map them with an explicit `mapping` {name: balloon}.

Nothing is written by `preview`. Every row is validated exactly as a typed value (name, sample,
characteristic, number, instrument register: range, resolution, calibration on the day) and gets its
verdict. `commit` writes all accepted rows in one append, each carrying the file's SHA-256 and its row
number, and keeps a read-only copy of the file next to the run - but only when no row was refused,
unless `skip_rejected` is set by the person importing. A value in inches for a millimetre
characteristic (or the reverse) is converted and the original is recorded with it; other unit
mismatches are refused.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import time
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
ALIASES = {
    "sample": ["sample", "serial", "serial number", "serial no", "sn", "s/n", "part", "part id", "part no"],
    "balloon": ["balloon", "balloon no", "balloon number", "char", "characteristic", "feature", "cid", "item", "no", "id"],
    "value": ["value", "actual", "measured", "measurement", "meas", "result"],
    "instrument": ["instrument", "gauge", "gage", "equipment"],
    "unit": ["unit", "units"],
    "note": ["note", "comment", "comments", "remarks"],
    "measured_at": ["date", "time", "measured at", "timestamp", "datetime"],
}
TO_MM = {"mm": 1.0, "in": 25.4, "inch": 25.4, '"': 25.4, "um": 0.001, "µm": 0.001}


def _norm(h) -> str:
    return re.sub(r"[\s_]+", " ", str(h or "").strip().lower()).rstrip(".:")


def read_table(name: str, data: bytes) -> list[list[str]]:
    if len(data) > MAX_BYTES:
        raise ValueError(f"file larger than {MAX_BYTES // 2**20} MiB")
    ext = Path(name).suffix.lower()
    if ext == ".xlsx":
        if not data.startswith(b"PK"):
            raise ValueError("not an .xlsx file (its leading bytes do not match)")
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).worksheets[0]
        return [["" if v is None else str(v) for v in r] for r in ws.iter_rows(values_only=True)]
    if ext in (".csv", ".txt", ".tsv"):
        text = data.decode("utf-8-sig", errors="strict") if _is_utf8(data) else data.decode("cp1252")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        return [r for r in csv.reader(io.StringIO(text), dialect)]
    raise ValueError("results must be .csv, .tsv, .txt or .xlsx")


def _is_utf8(b: bytes) -> bool:
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _balloon_index(plan: dict) -> tuple[dict, dict]:
    rows = plan.get("rows") or []
    return ({r["balloon_no"]: r for r in rows if r.get("balloon_no") is not None},
            {r["cid"].upper(): r for r in rows})


def _map_ref(name: str, mapping: dict | None) -> str | None:
    """The balloon a CMM feature name is mapped to: the exact name first, then a case-insensitive
    match only when it is unambiguous (names that differ only in case are not guessed between)."""
    if not mapping:
        return None
    if name in mapping:
        return str(mapping[name])
    hits = {str(v) for k, v in mapping.items() if str(k).lower() == name.lower()}
    return hits.pop() if len(hits) == 1 else None


def resolve_balloon(ref: str, plan: dict, mapping: dict | None = None) -> dict | None:
    by_no, by_cid = _balloon_index(plan)
    t = str(ref or "").strip()
    mapped = _map_ref(t, mapping)
    if mapped is not None:
        t = mapped
    if t.upper() in by_cid:
        return by_cid[t.upper()]
    m = re.fullmatch(r"(?:b|#|balloon|bal|char|item)?\s*[-.:#]?\s*(\d{1,4})(?:\.0+)?", t, re.I)
    if m:
        return by_no.get(int(m.group(1)))
    return None


def _header_map(header: list[str]) -> dict[str, int]:
    h = [_norm(x) for x in header]
    out = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in h and field not in out:
                out[field] = h.index(n)
    return out


def parse(name: str, data: bytes, plan: dict, *, default_sample: str | None = None,
          mapping: dict | None = None) -> tuple[list[dict], list[dict], str]:
    """(candidate rows as measurement bodies, refused rows with reasons, detected layout)."""
    table = [r for r in read_table(name, data) if any(str(c).strip() for c in r)]
    if len(table) < 2:
        raise ValueError("the file has no data rows")
    header, body = table[0], table[1:]
    hm = _header_map(header)
    cands, refused = [], []
    if "value" in hm and "balloon" in hm:
        layout = "long"
        for i, r in enumerate(body, start=2):
            cell = lambda f: (r[hm[f]].strip() if f in hm and hm[f] < len(r) else "")
            ref, sample = cell("balloon"), cell("sample") or (default_sample or "")
            row = resolve_balloon(ref, plan, mapping)
            if row is None:
                refused.append({"row": i, "ref": ref, "code": "unknown_ref",
                                "reason": f"no balloon or characteristic {ref!r} in this plan"})
                continue
            cands.append({"row": i, "ref": ref, "cid": row["cid"], "sample": sample, "value": cell("value"),
                          "instrument": cell("instrument"), "unit": cell("unit"), "note": cell("note"),
                          "measured_at": cell("measured_at")})
    else:
        layout = "wide"
        samples = [str(s).strip() for s in header[1:]]
        if not samples or not all(samples):
            raise ValueError("not a recognised layout: a long table needs 'balloon' and 'value' columns; "
                             "a wide table needs sample names as the headers of columns 2..n")
        for i, r in enumerate(body, start=2):
            ref = str(r[0]).strip() if r else ""
            row = resolve_balloon(ref, plan, mapping)
            if row is None:
                refused.append({"row": i, "ref": ref, "code": "unknown_ref",
                                "reason": f"no balloon or characteristic {ref!r} in this plan"})
                continue
            for j, s in enumerate(samples, start=1):
                v = str(r[j]).strip() if j < len(r) else ""
                if v:
                    cands.append({"row": i, "col": j + 1, "ref": ref, "cid": row["cid"], "sample": s, "value": v,
                                  "instrument": "", "unit": "", "note": "", "measured_at": ""})
    seen = {}
    for c in cands:
        k = (c["sample"], c["cid"])
        if k in seen:
            c["duplicate_of"] = seen[k]
        else:
            seen[k] = c["row"]
    return cands, refused, layout


def preview(name: str, data: bytes, plan: dict, *, inspector: str, register: dict | None,
            instrument: str = "", unit: str = "", default_sample: str | None = None,
            mapping: dict | None = None, profile: str = "") -> dict:
    from .measure import build_entry, verdict
    sha = hashlib.sha256(data).hexdigest()
    cands, refused, layout = parse(name, data, plan, default_sample=default_sample, mapping=mapping)
    rows = {r["cid"]: r for r in plan.get("rows") or []}
    accepted = []
    for c in cands:
        where = f"row {c['row']}" + (f", column {c['col']}" if c.get("col") else "")
        if c.get("duplicate_of"):
            refused.append({"row": c["row"], "ref": c["ref"], "where": where, "sha256": sha,
                            "reason": f"{c['sample']} / balloon "
                            f"{rows[c['cid']].get('balloon_no')} appears again (first at row {c['duplicate_of']}): "
                            "which value is right is not guessed"})
            continue
        body = {"inspector": inspector, "sample": c["sample"], "cid": c["cid"],
                "instrument": c["instrument"] or instrument, "note": c["note"],
                "source": {"file": name, "sha256": sha, "where": where}}
        if c.get("ref"):
            body["source"]["feature_name"] = c["ref"]
        if profile:
            body["source"]["mapping_profile"] = profile
        if c["measured_at"]:
            body["measured_at"] = c["measured_at"][:40]
        try:
            x = float(str(c["value"]).replace(",", "."))
        except ValueError:
            refused.append({"row": c["row"], "ref": c["ref"], "where": where, "sha256": sha,
                            "reason": f"value {c['value']!r} is not a number"})
            continue
        u = (c["unit"] or unit or "").strip().lower()
        want = (rows[c["cid"]].get("unit") or "").lower()
        if u and want and u != want:
            if u in TO_MM and want in TO_MM:
                body["converted_from"] = {"value": x, "unit": u}
                x = x * TO_MM[u] / TO_MM[want]
            else:
                refused.append({"row": c["row"], "ref": c["ref"], "where": where, "sha256": sha,
                                "reason": f"unit {u!r} for a characteristic in {want!r} is not converted"})
                continue
        body["value"] = x
        try:
            e = build_entry(plan, body, register)
        except ValueError as ex:
            refused.append({"row": c["row"], "ref": c["ref"], "where": where, "sha256": sha,
                            "reason": str(ex)})
            continue
        v, why = verdict(rows[c["cid"]], e["value"])
        is_mapped = _map_ref(c["ref"], mapping) is not None
        accepted.append({"entry": e, "verdict": v, "why": why, "where": where, "sha256": sha,
                         "ref": c["ref"], "cid": c["cid"],
                         "balloon_no": rows[c["cid"]].get("balloon_no"),
                         "mapped": is_mapped})
    refused.sort(key=lambda r: r["row"])
    return {"file": name, "sha256": sha, "layout": layout, "accepted": accepted, "refused": refused,
            "samples": sorted({a["entry"]["sample"] for a in accepted}),
            "mapping_profile": profile or None,
            "mapping": mapping or {},
            "summary": {"accepted": len(accepted), "refused": len(refused),
                        "fail": sum(1 for a in accepted if a["verdict"] == "FAIL")}}


def commit(plan_path: Path, name: str, data: bytes, plan: dict, *, inspector: str, register: dict | None,
           skip_rejected: bool = False, mapping: dict | None = None, profile: str = "", **kw) -> dict:
    from .measure import append
    pv = preview(name, data, plan, inspector=inspector, register=register, mapping=mapping,
                 profile=profile, **kw)
    if not pv["accepted"]:
        raise ValueError("nothing to import: no row was accepted")
    if pv["refused"] and not skip_rejected:
        raise ValueError(f"{len(pv['refused'])} row(s) refused; fix the file, or import the "
                         f"{len(pv['accepted'])} accepted row(s) explicitly (skip_rejected)")
    # preserve the exact file, read-only, beside the run
    keep_dir = plan_path.parent / "imports"
    keep_dir.mkdir(exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", Path(name).name)[:80]
    keep = keep_dir / f"{pv['sha256'][:16]}_{safe}"
    if not keep.exists():
        keep.write_bytes(data)
        os.chmod(keep, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    entries = [a["entry"] for a in pv["accepted"]]
    for e in entries:
        e["source"]["kept_as"] = f"imports/{keep.name}"
    append(plan_path, entries)
    stem = plan_path.name[:-len(".plan.json")]
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "by": inspector, "file": name,
           "sha256": pv["sha256"], "kept_as": f"imports/{keep.name}", "layout": pv["layout"],
           "imported": len(entries), "refused": len(pv["refused"]), "skip_rejected": bool(skip_rejected),
           "mapping_profile": profile or None}
    with plan_path.with_name(f"{stem}.imports.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {**pv, "committed": rec}


# ---- CMM mapping profiles per program -----------------------------------------------------

_PROGRAM_RE = re.compile(r"^[\w .'-]{1,64}$")


def mapping_profiles_path(project_dir: Path) -> Path:
    return Path(project_dir) / "cmm_mappings.json"


def load_mapping_profiles(project_dir: Path) -> dict[str, dict]:
    """Load all saved CMM mapping profiles for a project or run directory."""
    p = mapping_profiles_path(project_dir)
    if not p.is_file():
        return {}
    # Unreadable is an error, not "no profiles": read as empty, the next save would overwrite the
    # file and silently drop every other program's mapping.
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise ValueError(f"{p.name} could not be read ({type(ex).__name__}); it was left unchanged") from ex
    if not isinstance(data, dict):
        raise ValueError(f"{p.name} does not hold a set of profiles; it was left unchanged")
    return data


def save_mapping_profile(project_dir: Path, program: str, mappings: dict[str, Any], by: str = "") -> dict:
    """Save or update a CMM mapping profile (feature name -> balloon reference) for a program."""
    prog = str(program or "").strip()
    if not _PROGRAM_RE.fullmatch(prog):
        raise ValueError("program name must be 1 to 64 characters (letters, numbers, space, . ' - _)")
    if not isinstance(mappings, dict) or not mappings:
        raise ValueError("mappings must be a non-empty dictionary of feature name to balloon reference")
    cleaned_mappings: dict[str, str | int] = {}
    for k, v in mappings.items():
        k_str = str(k or "").strip()
        if not k_str:
            raise ValueError("mapping feature name cannot be empty")
        if isinstance(v, int):
            cleaned_mappings[k_str] = v
        else:
            v_str = str(v or "").strip()
            if not v_str:
                raise ValueError(f"mapping for {k_str!r} cannot be empty")
            if v_str.isdigit():
                cleaned_mappings[k_str] = int(v_str)
            else:
                cleaned_mappings[k_str] = v_str
    seen: dict[str, str] = {}
    for k_str in cleaned_mappings:
        if k_str.lower() in seen:
            raise ValueError(f"feature names {seen[k_str.lower()]!r} and {k_str!r} differ only in case: "
                             "which balloon is meant is not guessed")
        seen[k_str.lower()] = k_str

    project_dir = Path(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    profiles = load_mapping_profiles(project_dir)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rec = {
        "program": prog,
        "created_utc": profiles.get(prog, {}).get("created_utc", now),
        "updated_utc": now,
        "updated_by": str(by or "").strip()[:64],
        "mappings": cleaned_mappings,
    }
    profiles[prog] = rec
    p = mapping_profiles_path(project_dir)
    tmp = p.with_name("cmm_mappings.json.tmp")
    tmp.write_text(json.dumps(profiles, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return rec


def delete_mapping_profile(project_dir: Path, program: str) -> bool:
    """Delete a CMM mapping profile by program name."""
    project_dir = Path(project_dir)
    profiles = load_mapping_profiles(project_dir)
    prog = str(program or "").strip()
    if prog not in profiles:
        return False
    del profiles[prog]
    p = mapping_profiles_path(project_dir)
    tmp = p.with_name("cmm_mappings.json.tmp")
    tmp.write_text(json.dumps(profiles, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return True

