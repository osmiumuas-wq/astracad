"""Shared base for adapters that can discover an installation but have no tested automation path.

They answer GET_APPLICATION_STATUS truthfully and decline every other operation with
UNSUPPORTED_OPERATION and a reason. They never report CONNECTED, because no live integration
check exists for them yet.
"""
from __future__ import annotations

from ..contract import (Adapter, ConnectionState, Declaration, Evidence, Op, OperationResult,
                        OperationSupport, OpStatus, StatusReport, Support)
from ..probes import Probe, SystemProbe


class DiscoveryOnlyAdapter(Adapter):
    application = "abstract"
    mechanism = ""
    licensing = ""
    planned_api = ""

    def __init__(self, probe: Probe | None = None):
        self.probe = probe or SystemProbe()

    def declaration(self) -> Declaration:
        ops = {o: OperationSupport(Support.UNSUPPORTED, Evidence.NONE,
                                   f"not implemented: no {self.application} installation was "
                                   f"available to test {self.planned_api} against")
               for o in Op}
        ops[Op.GET_APPLICATION_STATUS] = OperationSupport(
            Support.SUPPORTED, Evidence.UNIT_TESTED,
            "read-only discovery (registry, install paths, process list); never reports CONNECTED")
        return Declaration(self.application, "0.1.0", [], self.mechanism, ops,
                           ["Windows host"], ["read registry and process list"], self.licensing,
                           ["installation discovery only"])

    def _find(self) -> tuple[str | None, str | None, dict]:  # pragma: no cover - abstract
        raise NotImplementedError

    def status(self) -> StatusReport:
        if self.probe.platform != "win32":
            return StatusReport(self.application, ConnectionState.NOT_INSTALLED,
                                f"discovery runs on Windows only (this host: {self.probe.platform})")
        path, version, facts = self._find()
        if path is None:
            return StatusReport(self.application, ConnectionState.NOT_INSTALLED,
                                "no installation found", facts=facts)
        return StatusReport(self.application, ConnectionState.API_UNAVAILABLE,
                            f"installed, but ASTraCAD has no tested {self.planned_api} bridge yet",
                            version=version, install_path=path, facts=facts)

    def _op_get_application_status(self, params) -> OperationResult:
        s = self.status()
        return OperationResult(self.name, Op.GET_APPLICATION_STATUS, OpStatus.SUCCEEDED, s.detail,
                               data=s.to_dict(), evidence=Evidence.UNIT_TESTED)
