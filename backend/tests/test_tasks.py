from datetime import timedelta

import pytest
from conftest import Clock

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.discovery import enqueue_discovery
from youmo_workflow.features.tasks import add_task
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task


def test_sync_trigger_dedup(runtime: Runtime, clock: Clock) -> None:
    first = enqueue_discovery(runtime.factory, clock=clock)
    again = enqueue_discovery(runtime.factory, clock=clock)
    assert first[0].id == again[0].id


def test_claim_is_exclusive(runtime: Runtime, clock: Clock) -> None:
    enqueue_discovery(runtime.factory, clock=clock)
    assert runtime.tasks.claim() is not None
    assert runtime.tasks.claim() is None


def test_stale_worker_cannot_finish(runtime: Runtime, clock: Clock) -> None:
    enqueue_discovery(runtime.factory, clock=clock)
    task = runtime.tasks.claim()
    assert task is not None
    clock.at += timedelta(minutes=16)
    assert not runtime.tasks.finish(task)
    assert runtime.tasks.recover() == 1
    replacement = runtime.tasks.claim()
    assert replacement is not None
    assert replacement.lease_token != task.lease_token
    assert not runtime.tasks.finish(task)
    assert runtime.tasks.finish(replacement)


def test_failed_read_can_retry_but_not_publish(runtime: Runtime, clock: Clock) -> None:
    with runtime.factory.begin() as db:
        task = add_task(db, "publish", "publish:test", stamp(clock()))
        task.status = "failed"
        task_id = task.id
    with pytest.raises(WorkflowError):
        runtime.tasks.retry(task_id)


def test_interrupted_publish_becomes_unknown(runtime: Runtime, clock: Clock) -> None:
    with runtime.factory.begin() as db:
        add_task(db, "publish", "publish:test", stamp(clock()))
    task = runtime.tasks.claim()
    assert task is not None
    clock.at += timedelta(minutes=121)
    runtime.tasks.recover()
    with runtime.factory() as db:
        row = db.get(Task, task.id)
        assert row and row.status == "unknown"
    assert runtime.tasks.claim() is None


def test_retry_budget_is_bounded(runtime: Runtime, clock: Clock) -> None:
    enqueue_discovery(runtime.factory, clock=clock)
    for _ in range(3):
        task = runtime.tasks.claim()
        assert task is not None
        runtime.tasks.fail(task, WorkflowError("NETWORK", "模拟瞬时故障", True))
        clock.at += timedelta(minutes=10)
    assert runtime.tasks.claim() is None
    assert runtime.tasks.list_tasks()[0].status == "failed"
