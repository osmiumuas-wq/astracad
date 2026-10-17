"""Universal CAD orchestrator: plan -> permission gate -> adapter -> verify -> record.

Every request becomes a persistent task. Read-only operations run without authorization;
mutating or persisting operations need an ``Authorization`` that names the person, the operation
scope and (for persisting operations) the output directories. A missing authorization parks the
task in WAITING_FOR_APPROVAL — it is not executed with a weaker operation instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from astracad_core.tasks import TaskState, TaskStore

from .contract import RISK, Adapter, Authorization, Op, OperationResult, OpStatus, Risk


@dataclass
class CadRequest:
    application: str
    operation: Op
    params: dict[str, Any]
    summary: str = ""


class Orchestrator:
    def __init__(self, adapters: dict[str, Adapter], store: TaskStore):
        self.adapters = adapters
        self.store = store

    def status_all(self) -> list[dict]:
        out = []
        for key, a in self.adapters.items():
            try:
                s = a.status().to_dict()
            except Exception as ex:          # discovery must never take the orchestrator down
                s = {"application": key, "state": "FAILED", "detail": f"{type(ex).__name__}: {ex}"}
            s["declaration"] = a.declaration().to_dict()
            out.append(s)
        return out

    def _gate(self, req: CadRequest, auth: Authorization | None) -> str | None:
        risk = RISK[req.operation]
        if risk is Risk.READ_ONLY:
            return None
        if auth is None or not auth.permits(req.operation):
            return (f"{req.operation.value} is {risk.value}; it needs an authorization naming a "
                    "person and this operation")
        if risk is Risk.PERSISTING or "out_dir" in req.params:
            out_dir = req.params.get("out_dir")
            if out_dir is None:
                return "no output directory given"
            target = Path(out_dir).resolve()
            allowed = [Path(d).resolve() for d in auth.allowed_output_dirs]
            if not any(target == a or a in target.parents for a in allowed):
                return f"output directory {target} is outside the authorised directories"
        return None

    def submit(self, req: CadRequest, auth: Authorization | None = None) -> tuple[str, OperationResult | None]:
        adapter = self.adapters.get(req.application)
        plan = [{"id": "s1", "op": req.operation.value, "application": req.application,
                 "irreversible": RISK.get(req.operation) is not Risk.READ_ONLY}]
        task = self.store.create(req.summary or f"{req.application}:{req.operation.value}",
                                 requirements={"params": _jsonable(req.params)}, plan=plan,
                                 max_retries=1)
        tid = task.task_id
        if adapter is None:
            self.store.record(tid, "error", text=f"unknown application {req.application!r}")
            self.store.transition(tid, TaskState.RUNNING, "start")
            self.store.transition(tid, TaskState.FAILED, "unknown application")
            return tid, None
        blocked = self._gate(req, auth)
        if blocked:
            self.store.record(tid, "note", text=blocked)
            self.store.transition(tid, TaskState.WAITING_FOR_APPROVAL, blocked)
            return tid, OperationResult(req.application, req.operation,
                                        OpStatus.REQUIRES_AUTHORIZATION, blocked)
        if auth is not None:
            self.store.record(tid, "approval", approver=auth.granted_by, note=auth.note,
                              scope=sorted(o.value for o in auth.scope))
        return tid, self._execute(tid, adapter, req)

    def resume(self, task_id: str, req: CadRequest, auth: Authorization) -> OperationResult:
        """Run a task that was waiting for approval, after a person authorised it."""
        blocked = self._gate(req, auth)
        if blocked:
            raise PermissionError(blocked)
        self.store.approve(task_id, auth.granted_by, auth.note)
        return self._execute(task_id, self.adapters[req.application], req)

    def _execute(self, tid: str, adapter: Adapter, req: CadRequest) -> OperationResult:
        self.store.transition(tid, TaskState.RUNNING, "start")
        self.store.record(tid, "step_started", step="s1")
        try:
            result = adapter.execute(req.operation, req.params)
        except Exception as ex:
            self.store.record(tid, "error", text=f"{type(ex).__name__}: {ex}")
            self.store.transition(tid, TaskState.FAILED, "adapter raised")
            raise
        self.store.record(tid, "tool_call", application=req.application, op=req.operation.value,
                          status=result.status.value, message=result.message,
                          argv=result.data.get("argv"))
        self.store.record(tid, "step_finished", step="s1")
        for a in result.artifacts:
            self.store.record(tid, "artifact", **a)
        for c in result.checks:
            self.store.record(tid, "verification", name=c.name, outcome=c.outcome,
                              expected=c.expected, actual=c.actual, why=c.why)
        for e in result.errors:
            self.store.record(tid, "error", text=str(e)[:2000])
        try:
            self._finish(tid, result)
        except Exception as ex:
            # the outcome could not be recorded as success: the task must still end, not hang
            self.store.record(tid, "error", text=f"finalising failed: {type(ex).__name__}: {ex}")
            self.store.transition(tid, TaskState.FAILED, f"finalising failed: {ex}",
                                  outcome="FINALISE_ERROR")
        return result

    def _finish(self, tid: str, result: OperationResult) -> None:
        if result.status is OpStatus.SUCCEEDED and result.checks:
            self.store.transition(tid, TaskState.SUCCEEDED, result.message,
                                  outcome=result.status.value)
        elif result.status is OpStatus.SUCCEEDED:
            # a read-only op with nothing to check: record the observation itself as evidence
            self.store.record(tid, "verification", name="operation_returned", outcome="passed",
                              expected="SUCCEEDED", actual=result.status.value, why=None)
            self.store.transition(tid, TaskState.SUCCEEDED, result.message,
                                  outcome=result.status.value)
        else:
            self.store.transition(tid, TaskState.FAILED, result.message,
                                  outcome=result.status.value)


def _jsonable(v):
    import json
    return json.loads(json.dumps(v, default=str))
