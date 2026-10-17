"""Versioned adapter contract shared by every CAD backend.

An adapter *declares* what it supports before anything runs, and every call returns an
``OperationResult`` whose status is one of a closed set. Two rules are structural here:

1. An operation the adapter does not declare as supported returns UNSUPPORTED_OPERATION. The
   orchestrator never substitutes a different operation for the one requested.
2. CONNECTED is only reported after a live integration check succeeded in this process; a file
   on disk or a registry key yields INSTALLED at most.
"""
from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any

CONTRACT_VERSION = "1.0.0"


class ConnectionState(str, enum.Enum):
    NOT_INSTALLED = "NOT_INSTALLED"
    INSTALLED = "INSTALLED"
    VERSION_UNSUPPORTED = "VERSION_UNSUPPORTED"
    API_UNAVAILABLE = "API_UNAVAILABLE"
    NOT_CONNECTED = "NOT_CONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    BUSY = "BUSY"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    REQUIRES_AUTHORIZATION = "REQUIRES_AUTHORIZATION"
    FAILED = "FAILED"
    DISCONNECTED = "DISCONNECTED"


class Op(str, enum.Enum):
    GET_APPLICATION_STATUS = "get_application_status"
    LIST_OPEN_DOCUMENTS = "list_open_documents"
    OPEN_DOCUMENT = "open_document"
    INSPECT_DOCUMENT = "inspect_document"
    INSPECT_MODEL_STRUCTURE = "inspect_model_structure"
    INSPECT_PARAMETERS = "inspect_parameters"
    CREATE_FEATURE = "create_feature"
    MODIFY_FEATURE = "modify_feature"
    INSPECT_ASSEMBLY = "inspect_assembly"
    MODIFY_ASSEMBLY = "modify_assembly"
    REGENERATE_MODEL = "regenerate_model"
    VALIDATE_GEOMETRY = "validate_geometry"
    EXPORT_DOCUMENT = "export_document"
    REOPEN_EXPORT = "reopen_export"
    SAVE_DOCUMENT = "save_document"
    CLOSE_DOCUMENT = "close_document"
    CREATE_DRAWING = "create_drawing"      # contract 0.2: drawing views + model dimensions


class Risk(str, enum.Enum):
    """How the permission gate treats an operation."""
    READ_ONLY = "read_only"          # inspects state; no authorization needed
    MUTATING = "mutating"            # changes the live session; needs an authorization
    PERSISTING = "persisting"        # writes files the user owns; needs an authorization naming paths


RISK: dict[Op, Risk] = {
    Op.GET_APPLICATION_STATUS: Risk.READ_ONLY, Op.LIST_OPEN_DOCUMENTS: Risk.READ_ONLY,
    Op.INSPECT_DOCUMENT: Risk.READ_ONLY, Op.INSPECT_MODEL_STRUCTURE: Risk.READ_ONLY,
    Op.INSPECT_PARAMETERS: Risk.READ_ONLY, Op.INSPECT_ASSEMBLY: Risk.READ_ONLY,
    Op.VALIDATE_GEOMETRY: Risk.READ_ONLY, Op.REOPEN_EXPORT: Risk.READ_ONLY,
    Op.OPEN_DOCUMENT: Risk.MUTATING, Op.CREATE_FEATURE: Risk.MUTATING,
    Op.MODIFY_FEATURE: Risk.MUTATING, Op.MODIFY_ASSEMBLY: Risk.MUTATING,
    Op.REGENERATE_MODEL: Risk.MUTATING, Op.CLOSE_DOCUMENT: Risk.MUTATING,
    Op.EXPORT_DOCUMENT: Risk.PERSISTING, Op.SAVE_DOCUMENT: Risk.PERSISTING,
    Op.CREATE_DRAWING: Risk.PERSISTING,
}


class Support(str, enum.Enum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"            # supported with stated restrictions
    UNSUPPORTED = "UNSUPPORTED"


class Evidence(str, enum.Enum):
    NONE = "NONE"
    UNIT_TESTED = "UNIT_TESTED"
    MOCK_INTEGRATION_TESTED = "MOCK_INTEGRATION_TESTED"
    REAL_APPLICATION_TESTED = "REAL_APPLICATION_TESTED"
    END_TO_END_VERIFIED = "END_TO_END_VERIFIED"


class OpStatus(str, enum.Enum):
    SUCCEEDED = "SUCCEEDED"                  # executed AND its own checks passed
    EXECUTED_UNVERIFIED = "EXECUTED_UNVERIFIED"  # the backend acted, but verification failed/was impossible
    FAILED = "FAILED"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    REQUIRES_AUTHORIZATION = "REQUIRES_AUTHORIZATION"
    NOT_CONNECTED = "NOT_CONNECTED"
    INVALID_REQUEST = "INVALID_REQUEST"


@dataclass(frozen=True)
class OperationSupport:
    support: Support
    evidence: Evidence
    restrictions: str = ""


@dataclass
class Declaration:
    application: str
    adapter_version: str
    supported_versions: list[str]
    connection_mechanism: str
    operations: dict[Op, OperationSupport]
    preconditions: list[str]
    permissions: list[str]
    licensing: str
    verification_methods: list[str]
    contract_version: str = CONTRACT_VERSION

    def supports(self, op: Op) -> bool:
        s = self.operations.get(op)
        return s is not None and s.support is not Support.UNSUPPORTED

    def unsupported(self) -> list[str]:
        return [o.value for o in Op if not self.supports(o)]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["operations"] = {o.value: {"support": s.support.value, "evidence": s.evidence.value,
                                     "restrictions": s.restrictions}
                           for o, s in self.operations.items()}
        d["unsupported_operations"] = self.unsupported()
        return d


@dataclass
class Check:
    name: str
    outcome: str        # "passed" | "failed" | "not_verifiable"
    expected: str
    actual: str
    why: str | None = None


@dataclass
class StatusReport:
    application: str
    state: ConnectionState
    detail: str
    version: str | None = None
    install_path: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


@dataclass
class OperationResult:
    application: str
    operation: Op
    status: OpStatus
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    evidence: Evidence = Evidence.NONE

    @property
    def ok(self) -> bool:
        return self.status is OpStatus.SUCCEEDED

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["operation"] = self.operation.value
        d["status"] = self.status.value
        d["evidence"] = self.evidence.value
        return d


@dataclass(frozen=True)
class Authorization:
    """An explicit, attributable permission for one task. Paths bound persisting operations."""
    granted_by: str
    scope: frozenset[Op]
    allowed_output_dirs: tuple[str, ...] = ()
    note: str = ""

    def permits(self, op: Op) -> bool:
        return bool(self.granted_by.strip()) and op in self.scope


class Adapter:
    """Base class. Subclasses override ``declaration``, ``status`` and the ``_op_*`` handlers
    they actually implement; anything else falls through to UNSUPPORTED_OPERATION."""

    name = "abstract"

    def declaration(self) -> Declaration:  # pragma: no cover - abstract
        raise NotImplementedError

    def status(self) -> StatusReport:  # pragma: no cover - abstract
        raise NotImplementedError

    def execute(self, op: Op, params: dict[str, Any]) -> OperationResult:
        decl = self.declaration()
        if not decl.supports(op):
            return OperationResult(self.name, op, OpStatus.UNSUPPORTED_OPERATION,
                                   f"{decl.application} adapter does not support {op.value}",
                                   errors=[decl.operations.get(op, OperationSupport(
                                       Support.UNSUPPORTED, Evidence.NONE)).restrictions
                                       or "not declared by the adapter"])
        handler = getattr(self, f"_op_{op.value}", None)
        if handler is None:
            return OperationResult(self.name, op, OpStatus.UNSUPPORTED_OPERATION,
                                   f"{op.value} declared but has no handler — declaration bug",
                                   errors=["declaration/handler mismatch"])
        return handler(params)
