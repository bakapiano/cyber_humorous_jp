from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import Clock
from pydantic import ValidationError
from selection_support import discovery_items
from sqlalchemy import select

from youmo_workflow.domain.batch_plan import BatchPlanUpdate, BatchTrigger
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.orchestration.scheduler import Scheduler
from youmo_workflow.runtime import Runtime, build_runtime
from youmo_workflow.storage.models import Batch, Task


def configure(runtime: Runtime, clock: Clock, *, auto: bool = False, count: int = 2) -> int:
    service = BatchPlanService(runtime.factory, runtime.policy, clock)
    return service.update(
        BatchPlanUpdate(
            batch_size=50,
            publications_per_batch=count,
            auto_enabled=auto,
            revision=service.state().revision,
            confirm_execution=auto,
        )
    ).revision


def manual(revision: int) -> BatchTrigger:
    return BatchTrigger(request_id=uuid4(), revision=revision, confirm_execution=True)


def finish_batch(runtime: Runtime, batch_id: str) -> None:
    with runtime.factory.begin() as db:
        row = db.get(Batch, batch_id)
        assert row is not None
        row.status = "completed"
        for task in db.scalars(select(Task).where(Task.batch_id == batch_id)):
            task.status = "succeeded"


def test_full_batches_same_day_are_exact_disjoint_and_idempotent(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(110), stamp(clock()))
    revision = configure(runtime, clock)
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock)
    request = manual(revision)
    first = trigger.trigger(request)
    assert first and first.candidate_count == 50 and first.publications_per_batch == 2
    assert trigger.trigger(request) == first
    clock.at += timedelta(seconds=1)
    second = trigger.trigger(manual(revision))
    assert second and second.id != first.id and second.local_date == first.local_date
    ids = {item.video_id for item in runtime.batches.detail(first.id).items}
    assert ids.isdisjoint(item.video_id for item in runtime.batches.detail(second.id).items)
    assert BatchPlanService(runtime.factory, runtime.policy, clock).state().eligible_count == 10
    finish_batch(runtime, second.id)
    with pytest.raises(WorkflowError, match="素材不足"):
        trigger.trigger(manual(revision))
    assert len(runtime.batches.list_batches()) == 2


def test_manual_and_automatic_both_require_a_full_batch(runtime: Runtime, clock: Clock) -> None:
    revision = configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(49), stamp(clock()))
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock)
    assert trigger.trigger() is None
    with pytest.raises(WorkflowError, match="49/50"):
        trigger.trigger(manual(revision))
    assert not runtime.tasks.list_tasks()


def test_plan_persists_hot_loads_and_preserves_old_snapshot(runtime: Runtime, clock: Clock) -> None:
    revision = configure(runtime, clock, count=2)
    runtime.sources.ingest("tbsnewsdig", discovery_items(100), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(revision))
    assert batch
    configure(runtime, clock, count=4)
    restarted = build_runtime(runtime.settings)
    assert (
        BatchPlanService(restarted.factory, restarted.policy, clock).state().publications_per_batch
        == 4
    )
    assert runtime.batches.detail(batch.id).publications_per_batch == 2
    restarted.engine.dispose()


def test_changed_plan_and_missing_authorization_rejected(runtime: Runtime, clock: Clock) -> None:
    service = BatchPlanService(runtime.factory, runtime.policy, clock)
    with pytest.raises(WorkflowError, match="确认模型费用"):
        service.update(BatchPlanUpdate(revision=0, auto_enabled=True))
    configure(runtime, clock)
    with pytest.raises(WorkflowError, match="重新加载"):
        service.update(BatchPlanUpdate(revision=0))
    with pytest.raises(WorkflowError, match="配置已改变"):
        BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(0))
    assert not runtime.tasks.list_tasks()


@pytest.mark.parametrize("size,count", [(49, 1), (1001, 1), (50, 0), (50, 31), (50.5, 1)])
def test_invalid_batch_sizes_and_counts(size: float, count: int) -> None:
    with pytest.raises(ValidationError):
        BatchPlanUpdate.model_validate(
            {"batch_size": size, "publications_per_batch": count, "revision": 0}
        )


def test_concurrent_manual_requests_create_one_batch(runtime: Runtime, clock: Clock) -> None:
    revision = configure(runtime, clock)
    runtime.sources.ingest("tbsnewsdig", discovery_items(110), stamp(clock()))
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock)
    request = manual(revision)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(trigger.trigger, [request, request]))
    assert results[0] and results[1] and results[0].id == results[1].id
    assert len(runtime.batches.list_batches()) == 1


def test_failure_is_isolated_and_followup_keeps_original_material_reserved(
    runtime: Runtime, clock: Clock
) -> None:
    configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(100), stamp(clock()))
    scheduler = Scheduler(runtime.factory, runtime.policy, clock)
    scheduler.tick()
    batch = runtime.batches.list_batches()[0]
    finish_batch(runtime, batch.id)
    runtime.batches.set_result(batch.id, "failed")
    with runtime.factory.begin() as db:
        task = db.scalar(select(Task).where(Task.batch_id == batch.id))
        assert task
        task.status = "failed"
    scheduler.tick()
    state = BatchPlanService(runtime.factory, runtime.policy, clock).state()
    assert not state.automatic_blocked and state.eligible_count == 0
    assert len(runtime.batches.list_batches()) == 2
    with runtime.factory() as db:
        row = db.get(Batch, batch.id)
        assert row and row.status == "failed" and not row.reservations_released
