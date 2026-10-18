"""SOLIDWORKS adapter — bridges to the `mac` runtime (mecagent-alternative, .NET 10, COM interop).

Division of labour:
- `mac` (C#) owns the live COM session: attach via the Running Object Table, build features,
  rebuild, measure mass properties from the kernel, SaveAs native + STEP, and write
  ``<spec>.report.json``. That runtime was executed against SOLIDWORKS 2026 SP04.1 on the
  user's PC (see reverse_engineering/FINDINGS_BRIEF.md §E).
- This adapter (Python) owns the contract: discovery, permission-gated invocation with an argv list
  (never a shell string), parsing the report, hashing artefacts, and an *independent* re-read of
  every exported STEP file (``step_check``) so the kernel's own numbers are cross-checked.

Known quirks of this install carried forward from the C# investigation: the kernel's bounding box
property returns zeros, and artefact hashes differ run to run (timestamps in the file). Neither is
trusted; the STEP re-read supplies the box and values, not hashes, are compared.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..contract import (Adapter, Check, ConnectionState, Declaration, Evidence, Op,
                        OperationResult, OperationSupport, OpStatus, StatusReport, Support)
from ..probes import Probe, SystemProbe
from ..step_check import read_step

WORKSPACE = Path(__file__).resolve().parents[4]          # ...\New folder (4)
MAC_PROJECT = WORKSPACE / "mecagent-alternative" / "src" / "Mac" / "Mac.csproj"
MAC_KINDS = {"sketch.rectangle", "sketch.circle", "sketch.profile", "sketch.loops", "feature.extrude",
             "feature.cut", "feature.revolve", "feature.pattern.linear", "feature.pattern.circular",
             "feature.fillet", "feature.chamfer", "feature.hole", "feature.parameter",
             "doc.note"}                                   # = OperationRegistry Implemented set
_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_OUTCOME = {0: "passed", 1: "failed", 2: "not_verifiable"}
SW_EXE = r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe"


def _sha256(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_spec(spec: dict[str, Any]) -> list[str]:
    """Structural checks done before anything reaches the CAD session."""
    errs = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    if not _NAME.match(str(spec.get("name", ""))):
        errs.append("name must match [A-Za-z0-9_-]{1,64} (it becomes a file name)")
    if spec.get("units") != "mm":
        errs.append("units must be 'mm' (the only unit the runtime's templates were verified in)")
    feats = spec.get("features")
    if not isinstance(feats, list) or not feats:
        errs.append("features must be a non-empty list")
        return errs
    ids = set()
    for i, f in enumerate(feats):
        kind = f.get("$op") if isinstance(f, dict) else None
        if kind not in MAC_KINDS:
            errs.append(f"features[{i}]: operation {kind!r} is not implemented by the runtime")
        fid = f.get("id") if isinstance(f, dict) else None
        if not fid or fid in ids:
            errs.append(f"features[{i}]: id missing or duplicated")
        ids.add(fid)
        for k, v in ((f.get("args") or {}).items() if isinstance(f, dict) else []):
            if isinstance(v, (int, float)) and k in ("width", "height", "depth", "radius",
                                                     "diameter", "distance", "spacing",
                                                     "angle") and v <= 0:
                errs.append(f"features[{i}].{k} must be > 0, got {v}")
    for e in spec.get("exports", []):
        if e.get("format") not in ("step", "sldprt"):
            errs.append(f"export format {e.get('format')!r} not supported by the runtime")
    return errs


def _dim_key(full: str) -> str:
    parts = full.split("@")
    return "@".join(parts[:2]) if len(parts) >= 2 else full


def drawing_dxf_checks(dxf: Path, model_dims: list[dict],
                       expected_title: dict | None = None,
                       expected_tolerance: str | None = None) -> list[Check]:
    """Independent check of a CAD-generated drawing, by the inspection engine rather than the CAD
    application: every dimension's printed value must agree with its drawn geometry, and the
    printed values must match the model's dimensions one to one (nothing missing, nothing extra)."""
    if not dxf.exists():
        return [Check("inspect:dxf_present", "failed", "exported DXF", "missing")]
    from astracad_inspect.ingest.dxf import read_dxf
    from astracad_inspect.plan import build_plan
    bundle = read_dxf(dxf)
    plan = build_plan(bundle)
    out = [Check("inspect:dxf_unit_mm", "passed" if bundle.unit == "mm" else "failed", "mm",
                 bundle.unit)]
    geo = [k for c in bundle.characteristics for k in c.checks if k.name == "geometry_cross_check"]
    bad = [k for k in geo if k.outcome.value != "passed"]
    out.append(Check("inspect:geometry_cross_check", "passed" if geo and not bad else "failed",
                     f"{len(geo)} dimension(s) agree with drawn geometry",
                     f"{len(geo) - len(bad)} agree"))
    remaining = [r for r in plan.rows if r.nominal is not None]
    for d in model_dims:
        mm = float(d["mm"])
        hit = next((r for r in remaining if abs(r.nominal - mm) <= 0.005 + 1e-9), None)
        if hit is not None:
            remaining.remove(hit)
        out.append(Check(f"inspect:printed_value:{_dim_key(d['name'])}",
                         "passed" if hit else "failed", f"{mm:g} mm printed",
                         hit.callout if hit else "not printed"))
    out.append(Check("inspect:no_extra_dimensions", "passed" if not remaining else "failed",
                     "every printed dimension is a model dimension",
                     ", ".join(r.callout for r in remaining) or "none extra"))
    if expected_title:
        # mac reports "title"; the DXF reader files a TITLE label under part_name
        for key, read_as in (("drawing_number", "drawing_number"), ("title", "part_name"),
                             ("revision", "revision"), ("material", "material")):
            if expected_title.get(key):                  # unstated (None) = nothing to compare
                expected_val = str(expected_title[key])
                actual_val = str(bundle.title_block.get(read_as) or "")
                out.append(Check(f"inspect:title_block:{key}",
                                 "passed" if actual_val.lower() == expected_val.lower() else "failed",
                                 expected_val, actual_val or "<absent>"))
    if expected_tolerance:
        from astracad_inspect.general_tol import detect
        gtol = detect(bundle.notes)
        found = bool(gtol.iso_class and gtol.iso_class in expected_tolerance.lower())
        out.append(Check("inspect:general_tolerance",
                         "passed" if found else "failed",
                         expected_tolerance, gtol.evidence or "<absent>"))
    return out


class SolidWorksAdapter(Adapter):
    name = "solidworks"

    def __init__(self, probe: Probe | None = None, mac: list[str] | None = None,
                 timeout_s: float = 600.0):
        self.probe = probe or SystemProbe()
        self._mac = mac
        self.timeout_s = timeout_s

    # ---- discovery --------------------------------------------------------------------------
    def mac_command(self) -> list[str] | None:
        if self._mac is not None:
            return self._mac
        env = self.probe.env("ASTRACAD_MAC")
        if env and self.probe.exists(env):
            return [env]
        for cfg in ("Release", "Debug"):
            exe = MAC_PROJECT.parent / "bin" / cfg / "net10.0-windows" / "mac.exe"
            if self.probe.exists(str(exe)):
                return [str(exe)]
        if self.probe.exists(str(MAC_PROJECT)) and self.probe.which("dotnet"):
            return ["dotnet", "run", "--project", str(MAC_PROJECT), "--"]
        return None

    def installed_versions(self) -> list[str]:
        return [k for k in self.probe.registry_subkeys("HKLM", r"SOFTWARE\SolidWorks")
                if k.upper().startswith("SOLIDWORKS 20")]

    def declaration(self) -> Declaration:
        mock = Evidence.MOCK_INTEGRATION_TESTED
        ops = {o: OperationSupport(Support.UNSUPPORTED, Evidence.NONE,
                                   "not implemented by the mac runtime") for o in Op}
        ops.update({
            Op.GET_APPLICATION_STATUS: OperationSupport(
                Support.SUPPORTED, Evidence.REAL_APPLICATION_TESTED,
                "via `mac discover` (ROT attach); NOT_CONNECTED and CONNECTED both observed live "
                "on SOLIDWORKS 2026 SP04.1, 2026-10-17"),
            Op.LIST_OPEN_DOCUMENTS: OperationSupport(Support.SUPPORTED, mock, "via `mac docs`"),
            Op.CREATE_FEATURE: OperationSupport(
                Support.PARTIAL, Evidence.END_TO_END_VERIFIED,
                "new part from a typed spec: rectangle/circle/profile sketch, extrude, cut, hole, "
                "revolve, linear pattern, circular pattern, fillet, chamfer, note; mirror/shell are "
                "not implemented. Live 2026-10-17: request/spec -> SOLIDWORKS -> kernel checks -> "
                "STEP -> independent re-read: block, shaft, holed block, stepped shaft (revolve), "
                "plate with 4-hole pattern all SUCCEEDED"),
            Op.MODIFY_FEATURE: OperationSupport(
                Support.PARTIAL, Evidence.END_TO_END_VERIFIED,
                "drives named dimensions (e.g. D1@Boss-Extrude1) either of a model built in the "
                "same run, or of an existing .sldprt via `mac edit`, which edits a COPY and proves "
                "the source hash unchanged; features cannot be added to existing parts. Live "
                "2026-10-17: block depth 20->35 and shaft length 100->120 SUCCEEDED; unknown "
                "dimension name FAILED with nothing substituted or left behind"),
            Op.OPEN_DOCUMENT: OperationSupport(
                Support.PARTIAL, Evidence.REAL_APPLICATION_TESTED,
                "read-only open via `mac inspect`; closes the document again if it opened it"),
            Op.INSPECT_PARAMETERS: OperationSupport(
                Support.SUPPORTED, Evidence.REAL_APPLICATION_TESTED,
                "driving dimensions by full name, values in mm (live 2026-10-17)"),
            Op.CREATE_DRAWING: OperationSupport(
                Support.PARTIAL, Evidence.END_TO_END_VERIFIED,
                "new .SLDDRW of an existing part via `mac drawing`: 3 standard views (1st/3rd angle), "
                "model dimensions inserted (duplicates eliminated), MMGS units, DXF + PDF export; "
                "part opened read-only, hash proven unchanged. Checked by mac (every model dimension "
                "on the drawing, equal value) AND independently by astracad_inspect on the DXF "
                "(geometry cross-check + printed values 1:1 with the model). No title block/sheet "
                "format, no tolerances added, parts only. Live 2026-10-17: holed block SUCCEEDED"),
            Op.VALIDATE_GEOMETRY: OperationSupport(Support.SUPPORTED, Evidence.UNIT_TESTED,
                                                   "re-evaluates a run report's kernel checks"),
            Op.REOPEN_EXPORT: OperationSupport(Support.SUPPORTED, Evidence.UNIT_TESTED,
                                               "independent STEP re-read (step_check)"),
        })
        ops[Op.EXPORT_DOCUMENT] = OperationSupport(
            Support.UNSUPPORTED, Evidence.NONE,
            "no standalone export; STEP/SLDPRT are written inside create/modify runs (spec.exports)")
        return Declaration(
            "SOLIDWORKS", "0.1.0", ["SOLIDWORKS 2026 (SP04.1 executed by mac)"],
            "COM via SolidWorks.Interop (early-bound .NET 10) in the `mac` runtime; Python calls "
            "mac as a subprocess with an argv list and reads its JSON report",
            ops,
            ["Windows", "SOLIDWORKS running with a user session (ROT-registered)",
             "mac built (dotnet build) or dotnet SDK present", "Part template resolvable"],
            ["create/modify parts in the live session", "write files to an authorised directory"],
            "Requires a licensed SOLIDWORKS seat; the API is part of the standard install",
            ["kernel mass properties read back by mac", "analytic expectation per spec",
             "independent STEP re-read: unit, solid count, bounding box, polyhedral volume",
             "artefact SHA-256"])

    def status(self) -> StatusReport:
        if self.probe.platform != "win32":
            return StatusReport("SOLIDWORKS", ConnectionState.NOT_INSTALLED,
                                f"SOLIDWORKS automation needs Windows (this host: "
                                f"{self.probe.platform})")
        versions = self.installed_versions()
        exe = self.probe.exists(SW_EXE)
        facts: dict[str, Any] = {"registry_versions": versions, "exe_present": exe}
        if not versions and not exe:
            return StatusReport("SOLIDWORKS", ConnectionState.NOT_INSTALLED,
                                "no SOLIDWORKS registry key or executable", facts=facts)
        mac = self.mac_command()
        facts["mac"] = mac
        if mac is None:
            return StatusReport("SOLIDWORKS", ConnectionState.API_UNAVAILABLE,
                                "installed, but the mac runtime is not built and dotnet is "
                                "unavailable", version=(versions or [None])[-1],
                                install_path=SW_EXE if exe else None, facts=facts)
        pids = self.probe.running("SLDWORKS.exe")
        facts["pids"] = pids
        if not pids:
            return StatusReport("SOLIDWORKS", ConnectionState.NOT_CONNECTED,
                                "installed; SOLIDWORKS is not running (start it and sign in)",
                                version=(versions or [None])[-1], install_path=SW_EXE,
                                facts=facts)
        try:
            cp = self.probe.run(mac + ["discover"], timeout=90)
        except Exception as ex:  # timeout, missing binary
            return StatusReport("SOLIDWORKS", ConnectionState.FAILED,
                                f"mac discover could not run: {type(ex).__name__}: {ex}",
                                facts=facts)
        facts["discover_stdout"] = cp.stdout[-2000:]
        m = re.search(r"SESSION\s+pid=(\d+)\s+base=(\S+)\s+sp=(\S+)\s+open_docs=(\d+)", cp.stdout)
        if cp.returncode == 0 and m:
            facts.update(pid=int(m.group(1)), open_docs=int(m.group(4)), sp=m.group(3))
            return StatusReport("SOLIDWORKS", ConnectionState.CONNECTED,
                                "live session attached through the Running Object Table",
                                version=m.group(2), install_path=SW_EXE, facts=facts)
        return StatusReport("SOLIDWORKS", ConnectionState.NOT_CONNECTED,
                            "process running but no automation object in the ROT yet "
                            "(still starting, or a modal dialog is open)", facts=facts)

    # ---- operations -------------------------------------------------------------------------
    def _op_get_application_status(self, params) -> OperationResult:
        s = self.status()
        st = OpStatus.SUCCEEDED if s.state is ConnectionState.CONNECTED else OpStatus.NOT_CONNECTED
        return OperationResult(self.name, Op.GET_APPLICATION_STATUS, st, s.detail,
                               data=s.to_dict(),
                               evidence=self.declaration().operations[Op.GET_APPLICATION_STATUS].evidence)

    def _require_connected(self, op: Op) -> OperationResult | None:
        s = self.status()
        if s.state is not ConnectionState.CONNECTED:
            return OperationResult(self.name, op, OpStatus.NOT_CONNECTED,
                                   f"SOLIDWORKS is {s.state.value}: {s.detail}",
                                   data=s.to_dict())
        return None

    def _op_list_open_documents(self, params) -> OperationResult:
        if (r := self._require_connected(Op.LIST_OPEN_DOCUMENTS)) is not None:
            return r
        cp = self.probe.run(self.mac_command() + ["docs"], timeout=60)
        m = re.search(r"DOCS\s+(\d+) open:\s*(.*)", cp.stdout)
        if cp.returncode != 0 or not m:
            return OperationResult(self.name, Op.LIST_OPEN_DOCUMENTS, OpStatus.FAILED,
                                   "mac docs did not report documents", errors=[cp.stdout[-800:]])
        titles = [t.strip() for t in m.group(2).split(",") if t.strip()]
        ok = len(titles) == int(m.group(1))
        return OperationResult(self.name, Op.LIST_OPEN_DOCUMENTS,
                               OpStatus.SUCCEEDED if ok else OpStatus.EXECUTED_UNVERIFIED,
                               f"{len(titles)} open document(s)", data={"titles": titles},
                               checks=[Check("count_matches_list", "passed" if ok else "failed",
                                             m.group(1), str(len(titles)))],
                               evidence=self.declaration().operations[Op.LIST_OPEN_DOCUMENTS].evidence)

    def _op_create_feature(self, params) -> OperationResult:
        return self._run_spec(Op.CREATE_FEATURE, params["spec"], Path(params["out_dir"]),
                              params.get("expect") or {}, bool(params.get("keep")),
                              bool(params.get("overwrite")))

    def _op_open_document(self, params) -> OperationResult:
        return self._inspect(Op.OPEN_DOCUMENT, params)

    def _op_inspect_parameters(self, params) -> OperationResult:
        return self._inspect(Op.INSPECT_PARAMETERS, params)

    def _inspect(self, op: Op, params) -> OperationResult:
        path = Path(params["path"])
        if path.suffix.lower() != ".sldprt":
            return OperationResult(self.name, op, OpStatus.INVALID_REQUEST,
                                   "only .sldprt parts are supported", errors=[str(path)])
        if not path.exists():
            return OperationResult(self.name, op, OpStatus.INVALID_REQUEST, "file not found",
                                   errors=[str(path)])
        if (r := self._require_connected(op)) is not None:
            return r
        before = _sha256(path)
        cp = self.probe.run(self.mac_command() + ["inspect", str(path)], timeout=180)
        try:
            data = json.loads(cp.stdout[cp.stdout.index("{"):cp.stdout.rindex("}") + 1])
        except ValueError:
            return OperationResult(self.name, op, OpStatus.FAILED, "mac inspect gave no report",
                                   errors=[cp.stdout[-1500:]])
        after = _sha256(path)
        checks = [Check("source_unchanged", "passed" if before == after else "failed",
                        before, after),
                  Check("dimensions_read", "passed" if data.get("dimensions") else "failed",
                        ">= 1 driving dimension", str(len(data.get("dimensions") or [])))]
        failed = [c for c in checks if c.outcome == "failed"]
        return OperationResult(self.name, op,
                               OpStatus.SUCCEEDED if cp.returncode == 0 and not failed
                               else OpStatus.EXECUTED_UNVERIFIED,
                               f"{len(data.get('dimensions') or [])} dimension(s) read", data=data,
                               checks=checks, evidence=self.declaration().operations[op].evidence)

    def _op_modify_feature(self, params) -> OperationResult:
        if "source_path" in params:
            return self._edit_existing(params)
        base = json.loads(json.dumps(params["base_spec"]))
        step = {"$op": "feature.parameter", "id": params.get("id", "modify"),
                "args": {"target": params["target"], "value": params["value"]}}
        if params.get("drives"):
            step["args"]["drives"] = params["drives"]
        base["features"].append(step)
        if params.get("name"):
            base["name"] = params["name"]
        return self._run_spec(Op.MODIFY_FEATURE, base, Path(params["out_dir"]),
                              params.get("expect") or {}, bool(params.get("keep")),
                              bool(params.get("overwrite")))

    def _op_create_drawing(self, params) -> OperationResult:
        op = Op.CREATE_DRAWING
        part = Path(params["part_path"])
        name, out_dir = str(params.get("name", "")), Path(params["out_dir"])
        angle = params.get("angle", "first")
        if part.suffix.lower() != ".sldprt" or not part.exists():
            return OperationResult(self.name, op, OpStatus.INVALID_REQUEST,
                                   "part must be an existing .sldprt", errors=[str(part)])
        if not _NAME.match(name) or angle not in ("first", "third"):
            return OperationResult(self.name, op, OpStatus.INVALID_REQUEST,
                                   "name must match [A-Za-z0-9_-]{1,64}; angle first|third")
        existing = [str(out_dir / f"{name}{e}") for e in (".SLDDRW", ".dxf", ".pdf", ".drawing.json")
                    if (out_dir / f"{name}{e}").exists()]
        if existing:
            return OperationResult(self.name, op, OpStatus.INVALID_REQUEST,
                                   "output files already exist; nothing was overwritten",
                                   errors=existing)
        if (r := self._require_connected(op)) is not None:
            return r
        before = _sha256(part)
        out_dir.mkdir(parents=True, exist_ok=True)
        argv = self.mac_command() + ["drawing", str(part), "--out", str(out_dir), "--name", name,
                                     "--angle", angle, "--export", "dxf,pdf"]
        for opt in ("title", "drawing_number", "revision", "material", "tolerance"):
            if opt in params and params[opt]:
                argv += [f"--{opt.replace('_', '-')}", str(params[opt])]
        if params.get("hidden_lines"):
            argv.append("--hidden-lines")
        try:
            cp = self.probe.run(argv, timeout=self.timeout_s)
        except Exception as ex:
            # Observed 2026-10-17: a run hung inside SOLIDWORKS; the timeout must become a FAILED
            # result (and a recorded task outcome), never an exception that leaves the task open.
            return OperationResult(self.name, op, OpStatus.FAILED,
                                   f"mac drawing did not complete: {type(ex).__name__}: {ex}",
                                   errors=[str(ex), "SOLIDWORKS may be left inside a command or with "
                                           "an unsaved drawing open: check it, press Esc, and close "
                                           "documents without saving before retrying"],
                                   data={"argv": argv})
        rp = out_dir / f"{name}.drawing.json"
        if not rp.exists():
            return OperationResult(self.name, op, OpStatus.FAILED, "mac drawing wrote no report",
                                   errors=[cp.stdout[-1500:]], data={"argv": argv})
        report = json.loads(rp.read_text(encoding="utf-8"))
        after = _sha256(part)
        checks = [Check("part_unchanged", "passed" if after == before else "failed", before, after)]
        for row in report.get("comparison") or []:
            checks.append(Check(f"kernel:model_dimension_on_drawing:{row['model_dimension']}",
                                "passed" if row.get("ok") else "failed", f"{row['model_mm']} mm",
                                f"{row.get('drawing_mm')} mm on {row.get('on_drawing')} view(s)"))
        dxf = out_dir / f"{name}.dxf"
        checks += drawing_dxf_checks(dxf, report.get("model_dimensions") or [],
                                    expected_title=report.get("title_block"),
                                    expected_tolerance=report.get("general_tolerance"))
        for item in report.get("missing") or []:          # stated, never counted as passed
            checks.append(Check(f"drawing:{item}", "not_verifiable", "stated by the user", "not stated",
                                why=f"no {item.replace('_', ' ')} was given (--{item.split('_')[-1]}); "
                                    "none was invented for the drawing"))
        artifacts = [{"path": str(p), "bytes": p.stat().st_size, "sha256": _sha256(p)}
                     for p in (out_dir / f"{name}.SLDDRW", dxf, out_dir / f"{name}.pdf")
                     if p.exists()]
        failed = [c for c in checks if c.outcome == "failed"]
        ok = cp.returncode == 0 and bool(report.get("success")) and not failed
        status = (OpStatus.SUCCEEDED if ok else
                  OpStatus.EXECUTED_UNVERIFIED if report.get("success") else OpStatus.FAILED)
        return OperationResult(self.name, op, status,
                               "drawing created and independently verified" if ok else
                               f"{len(failed)} check(s) failed",
                               data={"argv": argv, "report": report}, checks=checks,
                               artifacts=artifacts, errors=list(report.get("failures") or []),
                               evidence=self.declaration().operations[op].evidence)

    def _op_validate_geometry(self, params) -> OperationResult:
        rp = Path(params["report_path"])
        report = json.loads(rp.read_text(encoding="utf-8"))
        checks = self._mac_checks(report)
        failed = [c for c in checks if c.outcome == "failed"]
        return OperationResult(self.name, Op.VALIDATE_GEOMETRY,
                               OpStatus.SUCCEEDED if checks and not failed
                               else OpStatus.EXECUTED_UNVERIFIED,
                               f"{len(checks) - len(failed)}/{len(checks)} kernel checks passed",
                               data={"measured": report.get("measured")}, checks=checks,
                               evidence=Evidence.UNIT_TESTED)

    def _op_reopen_export(self, params) -> OperationResult:
        checks, data = self._step_checks(Path(params["path"]), params.get("expect") or {})
        failed = [c for c in checks if c.outcome == "failed"]
        return OperationResult(self.name, Op.REOPEN_EXPORT,
                               OpStatus.SUCCEEDED if not failed else OpStatus.EXECUTED_UNVERIFIED,
                               f"STEP re-read: {len(checks) - len(failed)}/{len(checks)} checks "
                               "passed", data=data, checks=checks, evidence=Evidence.UNIT_TESTED)

    def _edit_existing(self, params) -> OperationResult:
        op = Op.MODIFY_FEATURE
        src = Path(params["source_path"])
        out_dir = Path(params["out_dir"])
        name = str(params.get("name", ""))
        sets: dict = params.get("set") or {}
        errs = []
        if src.suffix.lower() != ".sldprt" or not src.exists():
            errs.append(f"source must be an existing .sldprt: {src}")
        if not _NAME.match(name):
            errs.append("name must match [A-Za-z0-9_-]{1,64}")
        elif name.lower() == src.stem.lower():
            errs.append("name must differ from the source file name")
        if not sets:
            errs.append("at least one dimension to set")
        for k, v in sets.items():
            if not re.match(r"^[A-Za-z][A-Za-z0-9_]*@[^=\s]+$", str(k)):
                errs.append(f"{k!r} is not a SOLIDWORKS dimension name like D1@Boss-Extrude1")
            if not isinstance(v, (int, float)) or v <= 0:
                errs.append(f"{k}: value must be a positive number of mm")
        if errs:
            return OperationResult(self.name, op, OpStatus.INVALID_REQUEST,
                                   "edit rejected before reaching SOLIDWORKS", errors=errs)
        existing = [str(p) for p in (out_dir / f"{name}.SLDPRT", out_dir / f"{name}.step",
                                     out_dir / f"{name}.edit.json") if p.exists()]
        if existing and not params.get("overwrite"):
            return OperationResult(self.name, op, OpStatus.INVALID_REQUEST,
                                   "output files already exist; nothing was overwritten",
                                   errors=existing)
        if (r := self._require_connected(op)) is not None:
            return r
        out_dir.mkdir(parents=True, exist_ok=True)
        src_before = _sha256(src)
        argv = self.mac_command() + ["edit", str(src), "--out", str(out_dir), "--name", name]
        for k, v in sets.items():
            argv += ["--set", f"{k}={float(v)!r}"]
        if params.get("export_step", True):
            argv += ["--export", "step"]
        try:
            cp = self.probe.run(argv, timeout=self.timeout_s)
        except Exception as ex:
            return OperationResult(self.name, op, OpStatus.FAILED,
                                   f"mac edit did not complete: {type(ex).__name__}: {ex}",
                                   errors=[str(ex)], data={"argv": argv})
        rp = out_dir / f"{name}.edit.json"
        if not rp.exists():
            return OperationResult(self.name, op, OpStatus.FAILED, "mac edit wrote no report",
                                   errors=[cp.stdout[-1500:], cp.stderr[-500:]],
                                   data={"argv": argv, "exit_code": cp.returncode})
        rep = json.loads(rp.read_text(encoding="utf-8-sig"))
        src_after = _sha256(src)
        checks = [Check("source_unchanged_python", "passed" if src_before == src_after
                        else "failed", src_before, src_after,
                        None if src_before == src_after else "the original part was modified"),
                  Check("source_unchanged_mac",
                        "passed" if (rep.get("source") or {}).get("unchanged") else "failed",
                        "true", str((rep.get("source") or {}).get("unchanged"))),
                  Check("no_new_rebuild_errors",
                        "passed" if rep.get("whats_wrong_after", -1) <= max(
                            rep.get("whats_wrong_before", 0), 0)
                        and rep.get("whats_wrong_after", -1) >= 0 else "failed",
                        f"<= {max(rep.get('whats_wrong_before', 0), 0)}",
                        str(rep.get("whats_wrong_after")))]
        for e in rep.get("edits") or []:
            got, want = e.get("after_mm"), e.get("requested_mm")
            ok = isinstance(got, (int, float)) and abs(got - want) <= 1e-9 * max(1.0, abs(want))
            checks.append(Check(f"kernel:dimension:{e.get('resolved') or e.get('target')}",
                                "passed" if ok else "failed", f"{want}", f"{got}",
                                None if ok else "read-back differs from the requested value"))
        expect = params.get("expect") or {}
        mass = rep.get("mass_after") or {}
        if "volume_mm3" in expect and isinstance(mass.get("volume_mm3"), (int, float)):
            want = float(expect["volume_mm3"])
            ok = abs(mass["volume_mm3"] - want) <= max(1e-6, 1e-6 * want)
            checks.append(Check("kernel:volume_mm3", "passed" if ok else "failed",
                                f"{want:.6f}", f"{mass['volume_mm3']:.6f}"))
        artifacts = []
        step_data = {}
        for a in rep.get("artifacts") or []:
            p = Path(a["path"])
            if not p.exists():
                checks.append(Check(f"artifact:{p.name}", "failed", "file exists", "missing"))
                continue
            artifacts.append({"path": str(p), "bytes": p.stat().st_size, "sha256": _sha256(p)})
            if p.suffix.lower() in (".step", ".stp"):
                cs, step_data[p.name] = self._step_checks(p, expect, mass.get("volume_mm3"))
                checks.extend(cs)
        failed = [c for c in checks if c.outcome == "failed"]
        independent_ok = any(c.name.startswith("step:") and c.outcome == "passed" for c in checks)
        edit_failed = any(not e.get("ok") for e in rep.get("edits") or [])
        if edit_failed:
            status, msg = OpStatus.FAILED, ("a requested dimension could not be set; nothing "
                                            "was substituted: " + "; ".join(rep.get("failures") or []))
        elif cp.returncode == 0 and rep.get("success") and not failed and independent_ok:
            status, msg = OpStatus.SUCCEEDED, "edited a copy in SOLIDWORKS; source untouched; every check passed"
        elif rep.get("success") or artifacts:
            status, msg = OpStatus.EXECUTED_UNVERIFIED, f"{len(failed)} check(s) failed — not accepted"
        else:
            status, msg = OpStatus.FAILED, "; ".join(rep.get("failures") or ["edit failed"])
        return OperationResult(self.name, op, status, msg,
                               data={"argv": argv, "exit_code": cp.returncode,
                                     "report_path": str(rp), "edits": rep.get("edits"),
                                     "mass_before": rep.get("mass_before"), "mass_after": mass,
                                     "step": step_data, "log": rep.get("log")},
                               checks=checks, artifacts=artifacts,
                               errors=list(rep.get("failures") or []),
                               evidence=self.declaration().operations[op].evidence)

    # ---- internals --------------------------------------------------------------------------
    @staticmethod
    def _mac_checks(report: dict) -> list[Check]:
        out = []
        for c in (report.get("verification") or {}).get("checks", []):
            o = c.get("outcome")
            outcome = _OUTCOME.get(o, o if isinstance(o, str) else "not_verifiable")
            exp, act = str(c.get("expected")), str(c.get("actual"))
            out.append(Check(f"kernel:{c.get('name')}", outcome, exp[:200], act[:200],
                             c.get("why") or (None if outcome == "passed" else
                                              f"delta={c.get('delta')} tolerance={c.get('tolerance')}")))
        return out

    @staticmethod
    def _occ_checks(spec: dict, measured: dict, step_data: dict) -> tuple[list[Check], dict]:
        """The second kernel: OpenCascade builds the same spec; its numbers must agree with SOLIDWORKS's."""
        try:
            from astracad_geom.compare import compare
            step = next(iter(step_data.values()), {}) if step_data else {}
            found, data = compare(spec, measured, step.get("bbox_mm"), bool(step.get("bbox_approximate", True)))
        except Exception as ex:          # the second kernel must never break a SOLIDWORKS build's report
            return [Check("occ:build", "not_verifiable", "an OpenCascade build of the spec", "error",
                          f"{type(ex).__name__}: {ex}")], {}
        return [Check(c["name"], c["outcome"], c["expected"], c["actual"], c["why"]) for c in found], data

    @staticmethod
    def _step_checks(path: Path, expect: dict, kernel_volume: float | None = None
                     ) -> tuple[list[Check], dict]:
        if not path.exists():
            return [Check(f"step:{path.name}:exists", "failed", "file", "missing")], {}
        r = read_step(path)
        data = {"schema": r.schema, "originating_system": r.originating_system,
                "unit": r.length_unit, "solids": r.solids, "faces": r.faces,
                "bbox_mm": r.bbox_mm, "size_mm": r.size_mm, "bbox_approximate": r.bbox_approximate,
                "volume_mm3": r.volume_mm3, "volume_reason": r.volume_reason, "errors": r.errors}
        n = path.name
        cs = [Check(f"step:{n}:parses", "passed" if not r.errors else "failed", "no errors",
                    "; ".join(r.errors) or "none"),
              Check(f"step:{n}:one_solid", "passed" if r.solids == 1 else "failed", "1",
                    str(r.solids)),
              Check(f"step:{n}:unit_mm", "passed" if r.length_unit == "mm" else "failed", "mm",
                    str(r.length_unit))]
        tol = float(expect.get("rel_tol", 1e-6))
        if "size_mm" in expect:
            want = sorted(float(x) for x in expect["size_mm"])
            got = sorted(r.size_mm or [])
            ok = len(got) == 3 and all(abs(a - b) <= max(1e-6, tol * b) for a, b in zip(got, want))
            cs.append(Check(f"step:{n}:size_mm", "passed" if ok else "failed",
                            str(want), str([round(x, 6) for x in got]),
                            None if ok else "bounding box differs from the requested size"
                            + (" (box approximate)" if r.bbox_approximate else "")))
        for label, want in (("expected", expect.get("volume_mm3")), ("kernel", kernel_volume)):
            if want is None:
                continue
            if r.volume_mm3 is None:
                cs.append(Check(f"step:{n}:volume_vs_{label}", "not_verifiable", f"{want:.6f}",
                                "not computed", r.volume_reason))
            else:
                ok = abs(r.volume_mm3 - want) <= max(1e-6, tol * want)
                cs.append(Check(f"step:{n}:volume_vs_{label}", "passed" if ok else "failed",
                                f"{want:.6f}", f"{r.volume_mm3:.6f}"))
        return cs, data

    def _run_spec(self, op: Op, spec: dict, out_dir: Path, expect: dict,
                  keep: bool, overwrite: bool = False) -> OperationResult:
        errs = validate_spec(spec)
        if errs:
            return OperationResult(self.name, op, OpStatus.INVALID_REQUEST,
                                   "spec rejected before reaching SOLIDWORKS", errors=errs)
        if (r := self._require_connected(op)) is not None:
            return r
        targets = [out_dir / f"{spec['name']}{ext}" for ext in
                   (".spec.json", ".report.json", ".sldprt", ".SLDPRT", ".step")]
        existing = sorted({str(t) for t in targets if t.exists()})
        if existing and not overwrite:
            return OperationResult(self.name, op, OpStatus.INVALID_REQUEST,
                                   "output files already exist; nothing was overwritten",
                                   errors=existing, data={"hint": "choose another out_dir or "
                                                                  "name, or pass overwrite=True"})
        out_dir.mkdir(parents=True, exist_ok=True)
        spec_path = out_dir / f"{spec['name']}.spec.json"
        spec_path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        argv = self.mac_command() + ["run", str(spec_path), "--out", str(out_dir)]
        if keep:
            argv.append("--keep")
        try:
            cp = self.probe.run(argv, timeout=self.timeout_s)
        except Exception as ex:
            return OperationResult(self.name, op, OpStatus.FAILED,
                                   f"mac run did not complete: {type(ex).__name__}: {ex}",
                                   errors=[str(ex)], data={"argv": argv})
        report_path = out_dir / f"{spec['name']}.report.json"
        if not report_path.exists():
            return OperationResult(self.name, op, OpStatus.FAILED,
                                   "mac run wrote no report; nothing can be verified",
                                   errors=[cp.stdout[-1500:], cp.stderr[-500:]],
                                   data={"argv": argv, "exit_code": cp.returncode})
        report = json.loads(report_path.read_text(encoding="utf-8"))
        execution = report.get("execution") or {}
        measured = report.get("measured") or {}
        checks = self._mac_checks(report)
        artifacts, step_data = [], {}
        for a in execution.get("artifacts") or execution.get("Artifacts") or []:
            p = Path(a)
            if not p.exists():                       # path may be reported with different case
                cand = out_dir / p.name
                p = cand if cand.exists() else p
            if not p.exists():
                checks.append(Check(f"artifact:{p.name}", "failed", "file exists", "missing"))
                continue
            artifacts.append({"path": str(p), "bytes": p.stat().st_size, "sha256": _sha256(p)})
            if p.suffix.lower() in (".step", ".stp"):
                cs, step_data[p.name] = self._step_checks(p, expect, measured.get("volume_mm3"))
                checks.extend(cs)
        if not any(c.name.startswith("step:") for c in checks):
            checks.append(Check("independent_reread", "not_verifiable", "a STEP export to re-read",
                                "none exported", "add {'format':'step'} to spec.exports"))
        occ_checks, occ_data = self._occ_checks(spec, measured, step_data)
        checks.extend(occ_checks)
        failed = [c for c in checks if c.outcome == "failed"]
        success = bool(execution.get("success", execution.get("Success")))
        independent_ok = any(c.name.startswith("step:") and c.outcome == "passed" for c in checks)
        if cp.returncode == 0 and success and not failed and independent_ok:
            status, msg = OpStatus.SUCCEEDED, "executed in SOLIDWORKS and every check passed"
        elif success:
            status, msg = OpStatus.EXECUTED_UNVERIFIED, (f"executed, but {len(failed)} check(s) "
                                                         "failed — result not accepted")
        else:
            status, msg = OpStatus.FAILED, "the runtime reported execution failure"
        return OperationResult(self.name, op, status, msg,
                               data={"argv": argv, "exit_code": cp.returncode,
                                     "report_path": str(report_path), "measured": measured,
                                     "log": execution.get("log") or execution.get("Log"),
                                     "step": step_data, "occ": occ_data, "session": report.get("session")},
                               checks=checks, artifacts=artifacts,
                               errors=list(execution.get("failures") or []),
                               evidence=self.declaration().operations[op].evidence)
