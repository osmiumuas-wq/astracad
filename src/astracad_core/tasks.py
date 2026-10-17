"""Persistent workflow engine (constitution Part 16).

One SQLite file holds every task, its state transitions, tool calls, artefacts, verification
results, errors and retry history. State survives a restart because nothing lives only in memory.

Rules enforced here rather than left to callers:
- Transitions follow an explicit table; an illegal transition raises instead of being recorded.
- A step marked ``irreversible`` that has already started is never re-run automatically: a retry
  of such a task stops in WAITING_FOR_APPROVAL so a person decides.
- Retries are bounded per task (``max_retries``).
- SUCCEEDED requires at least one *passed* verification and none *failed*. A ``not_verifiable``
  check (e.g. a quantity an independent method cannot compute) is recorded and reported but does
  not block; it can never be the only evidence. An agent's own claim of success is not evidence.
"""
from __future__ import annotations

import enum
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class TaskState(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    RECOVERING = "RECOVERING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}

ALLOWED: dict[TaskState, set[TaskState]] = {
    TaskState.QUEUED: {TaskState.RUNNING, TaskState.WAITING_FOR_APPROVAL,
                       TaskState.WAITING_FOR_INPUT, TaskState.CANCELLED},
    TaskState.RUNNING: {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.RECOVERING,
                        TaskState.WAITING_FOR_INPUT, TaskState.WAITING_FOR_APPROVAL,
                        TaskState.CANCELLED},
    TaskState.WAITING_FOR_INPUT: {TaskState.QUEUED, TaskState.CANCELLED},
    TaskState.WAITING_FOR_APPROVAL: {TaskState.QUEUED, TaskState.CANCELLED, TaskState.FAILED},
    TaskState.RECOVERING: {TaskState.QUEUED, TaskState.FAILED, TaskState.WAITING_FOR_APPROVAL,
                           TaskState.CANCELLED},
    TaskState.SUCCEEDED: set(),
    TaskState.FAILED: {TaskState.RECOVERING},
    TaskState.CANCELLED: set(),
}


class TransitionError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Task:
    task_id: str
    request: str
    state: TaskState
    requirements: dict[str, Any]
    plan: list[dict[str, Any]]
    depends_on: list[str]
    progress: float
    max_retries: int
    retries: int
    cancel_requested: bool
    outcome: str | None
    created_utc: str
    updated_utc: str
    events: list[dict[str, Any]] = field(default_factory=list)

    def of_kind(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] == kind]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, request TEXT NOT NULL, state TEXT NOT NULL,
  requirements TEXT NOT NULL, plan TEXT NOT NULL, depends_on TEXT NOT NULL,
  progress REAL NOT NULL, max_retries INTEGER NOT NULL, retries INTEGER NOT NULL,
  cancel_requested INTEGER NOT NULL, outcome TEXT, created_utc TEXT NOT NULL,
  updated_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(task_id),
  at_utc TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_task ON events(task_id, seq);
"""

EVENT_KINDS = {"state", "tool_call", "artifact", "verification", "error", "retry", "note",
               "step_started", "step_finished", "approval", "input"}


class TaskStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level="IMMEDIATE")
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ---- creation and reading -------------------------------------------------------------
    def create(self, request: str, requirements: dict | None = None,
               plan: list[dict] | None = None, depends_on: list[str] | None = None,
               max_retries: int = 2) -> Task:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        tid = "T-" + uuid.uuid4().hex[:12]
        now = _now()
        with self._db() as db:
            db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (tid, request, TaskState.QUEUED.value, json.dumps(requirements or {}),
                        json.dumps(plan or []), json.dumps(depends_on or []), 0.0,
                        max_retries, 0, 0, None, now, now))
            self._event(db, tid, "state", {"to": TaskState.QUEUED.value, "from": None})
        return self.get(tid)

    def get(self, task_id: str) -> Task:
        with self._db() as db:
            row = db.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            evs = db.execute("SELECT seq, at_utc, kind, payload FROM events WHERE task_id=? "
                             "ORDER BY seq", (task_id,)).fetchall()
        return Task(row[0], row[1], TaskState(row[2]), json.loads(row[3]), json.loads(row[4]),
                    json.loads(row[5]), row[6], row[7], row[8], bool(row[9]), row[10], row[11],
                    row[12], [{"seq": s, "at_utc": a, "kind": k, **json.loads(p)}
                              for s, a, k, p in evs])

    def list(self, state: TaskState | None = None) -> list[Task]:
        with self._db() as db:
            q = "SELECT task_id FROM tasks" + (" WHERE state=?" if state else "") + \
                " ORDER BY created_utc"
            ids = [r[0] for r in db.execute(q, (state.value,) if state else ())]
        return [self.get(i) for i in ids]

    # ---- mutation ---------------------------------------------------------------------------
    def _event(self, db: sqlite3.Connection, tid: str, kind: str, payload: dict) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        db.execute("INSERT INTO events(task_id, at_utc, kind, payload) VALUES (?,?,?,?)",
                   (tid, _now(), kind, json.dumps(payload, default=str)))
        db.execute("UPDATE tasks SET updated_utc=? WHERE task_id=?", (_now(), tid))

    def record(self, task_id: str, kind: str, **payload: Any) -> None:
        with self._db() as db:
            self._require(db, task_id)
            self._event(db, task_id, kind, payload)

    def _require(self, db: sqlite3.Connection, tid: str) -> TaskState:
        row = db.execute("SELECT state FROM tasks WHERE task_id=?", (tid,)).fetchone()
        if row is None:
            raise KeyError(tid)
        return TaskState(row[0])

    def transition(self, task_id: str, to: TaskState, reason: str = "",
                   outcome: str | None = None) -> Task:
        task = self.get(task_id)
        if to not in ALLOWED[task.state]:
            raise TransitionError(f"{task_id}: {task.state.value} -> {to.value} is not allowed")
        if to is TaskState.SUCCEEDED:
            ver = task.of_kind("verification")
            if not ver:
                raise TransitionError(f"{task_id}: SUCCEEDED needs at least one recorded "
                                      "verification; a step's own success claim is not evidence")
            failed = [v for v in ver if v.get("outcome") == "failed"]
            if failed:
                raise TransitionError(f"{task_id}: {len(failed)} verification(s) failed")
            if not any(v.get("outcome") == "passed" for v in ver):
                raise TransitionError(f"{task_id}: no verification passed; not_verifiable alone "
                                      "is not evidence of success")
        if to is TaskState.RUNNING:
            blockers = [d for d in task.depends_on if self.get(d).state is not TaskState.SUCCEEDED]
            if blockers:
                raise TransitionError(f"{task_id}: dependencies not succeeded: {blockers}")
        with self._db() as db:
            db.execute("UPDATE tasks SET state=?, outcome=COALESCE(?, outcome) WHERE task_id=?",
                       (to.value, outcome, task_id))
            if to is TaskState.SUCCEEDED:
                db.execute("UPDATE tasks SET progress=1.0 WHERE task_id=?", (task_id,))
            self._event(db, task_id, "state", {"from": task.state.value, "to": to.value,
                                                "reason": reason})
        return self.get(task_id)

    def set_progress(self, task_id: str, progress: float) -> None:
        if not 0.0 <= progress <= 1.0:
            raise ValueError("progress must be within [0, 1]")
        with self._db() as db:
            self._require(db, task_id)
            db.execute("UPDATE tasks SET progress=? WHERE task_id=?", (progress, task_id))

    def request_cancel(self, task_id: str, reason: str = "") -> Task:
        """Safe cancellation: queued/waiting tasks stop now; a running task is flagged and the
        runner stops at the next step boundary (a CAD call in flight is not torn down)."""
        task = self.get(task_id)
        if task.state in TERMINAL:
            return task
        with self._db() as db:
            db.execute("UPDATE tasks SET cancel_requested=1 WHERE task_id=?", (task_id,))
            self._event(db, task_id, "note", {"text": f"cancel requested: {reason}"})
        if task.state is not TaskState.RUNNING:
            return self.transition(task_id, TaskState.CANCELLED, reason or "cancel requested")
        return self.get(task_id)

    def retry(self, task_id: str, reason: str) -> Task:
        """Bounded retry of a FAILED task. Irreversible steps already started are not repeated
        blindly: the task parks in WAITING_FOR_APPROVAL instead."""
        task = self.get(task_id)
        if task.state is not TaskState.FAILED:
            raise TransitionError(f"{task_id}: only FAILED tasks can be retried")
        if task.retries >= task.max_retries:
            raise TransitionError(f"{task_id}: retry budget exhausted ({task.max_retries})")
        started = {e["step"] for e in task.of_kind("step_started")}
        irreversible_started = [s["id"] for s in task.plan
                                if s.get("irreversible") and s["id"] in started]
        with self._db() as db:
            db.execute("UPDATE tasks SET retries=retries+1 WHERE task_id=?", (task_id,))
            self._event(db, task_id, "retry", {"attempt": task.retries + 1, "reason": reason,
                                                "irreversible_started": irreversible_started})
        self.transition(task_id, TaskState.RECOVERING, reason)
        if irreversible_started:
            return self.transition(task_id, TaskState.WAITING_FOR_APPROVAL,
                                   f"irreversible step(s) {irreversible_started} already started;"
                                   " a person must confirm before they run again")
        return self.transition(task_id, TaskState.QUEUED, "retry")

    def approve(self, task_id: str, approver: str, note: str = "") -> Task:
        if not approver.strip():
            raise ValueError("an approval needs a named approver")
        self.record(task_id, "approval", approver=approver, note=note)
        return self.transition(task_id, TaskState.QUEUED, f"approved by {approver}")

    def recover_interrupted(self) -> list[str]:
        """After a restart, any task left RUNNING was interrupted mid-flight. It is moved to
        RECOVERING (never straight back to RUNNING), so its side effects can be inspected."""
        moved = []
        for t in self.list(TaskState.RUNNING):
            self.transition(t.task_id, TaskState.RECOVERING, "process restarted while running")
            moved.append(t.task_id)
        return moved
