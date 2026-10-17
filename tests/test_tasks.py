import pytest

from astracad_core.tasks import TaskState, TaskStore, TransitionError


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def test_success_requires_passed_verification(store):
    t = store.create("req")
    store.transition(t.task_id, TaskState.RUNNING)
    with pytest.raises(TransitionError):
        store.transition(t.task_id, TaskState.SUCCEEDED)
    store.record(t.task_id, "verification", name="v", outcome="failed")
    with pytest.raises(TransitionError):
        store.transition(t.task_id, TaskState.SUCCEEDED)


def test_happy_path_and_persistence(tmp_path):
    s = TaskStore(tmp_path / "x.db")
    t = s.create("req", plan=[{"id": "s1"}])
    s.transition(t.task_id, TaskState.RUNNING)
    s.record(t.task_id, "verification", name="v", outcome="passed")
    s.transition(t.task_id, TaskState.SUCCEEDED)
    again = TaskStore(tmp_path / "x.db").get(t.task_id)       # a new process, same file
    assert again.state is TaskState.SUCCEEDED and again.progress == 1.0
    assert [e["to"] for e in again.of_kind("state")] == ["QUEUED", "RUNNING", "SUCCEEDED"]


def test_illegal_transitions(store):
    t = store.create("r")
    with pytest.raises(TransitionError):
        store.transition(t.task_id, TaskState.SUCCEEDED)
    store.transition(t.task_id, TaskState.CANCELLED)
    with pytest.raises(TransitionError):
        store.transition(t.task_id, TaskState.RUNNING)


def test_bounded_retry(store):
    t = store.create("r", max_retries=1, plan=[{"id": "s1"}])
    store.transition(t.task_id, TaskState.RUNNING)
    store.transition(t.task_id, TaskState.FAILED)
    assert store.retry(t.task_id, "transient").state is TaskState.QUEUED
    store.transition(t.task_id, TaskState.RUNNING)
    store.transition(t.task_id, TaskState.FAILED)
    with pytest.raises(TransitionError):
        store.retry(t.task_id, "again")


def test_irreversible_step_is_not_repeated_blindly(store):
    t = store.create("r", plan=[{"id": "s1", "irreversible": True}])
    store.transition(t.task_id, TaskState.RUNNING)
    store.record(t.task_id, "step_started", step="s1")
    store.transition(t.task_id, TaskState.FAILED)
    t2 = store.retry(t.task_id, "network")
    assert t2.state is TaskState.WAITING_FOR_APPROVAL
    with pytest.raises(ValueError):
        store.approve(t.task_id, "  ")
    assert store.approve(t.task_id, "KM").state is TaskState.QUEUED


def test_cancel(store):
    q = store.create("q")
    assert store.request_cancel(q.task_id).state is TaskState.CANCELLED
    r = store.create("r")
    store.transition(r.task_id, TaskState.RUNNING)
    r2 = store.request_cancel(r.task_id, "user")
    assert r2.state is TaskState.RUNNING and r2.cancel_requested


def test_restart_recovery(store):
    t = store.create("r")
    store.transition(t.task_id, TaskState.RUNNING)
    assert store.recover_interrupted() == [t.task_id]
    assert store.get(t.task_id).state is TaskState.RECOVERING


def test_dependencies_block_start(store):
    a = store.create("a")
    b = store.create("b", depends_on=[a.task_id])
    with pytest.raises(TransitionError):
        store.transition(b.task_id, TaskState.RUNNING)


def test_not_verifiable_alone_is_not_success_but_does_not_block(store):
    t = store.create("r")
    store.transition(t.task_id, TaskState.RUNNING)
    store.record(t.task_id, "verification", name="a", outcome="not_verifiable")
    with pytest.raises(TransitionError, match="no verification passed"):
        store.transition(t.task_id, TaskState.SUCCEEDED)
    store.record(t.task_id, "verification", name="b", outcome="passed")
    assert store.transition(t.task_id, TaskState.SUCCEEDED).state is TaskState.SUCCEEDED
