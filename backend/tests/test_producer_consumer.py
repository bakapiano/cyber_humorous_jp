from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import Clock
from selection_support import discovery_items
from sqlalchemy import select
from test_batch_trigger import manual

from youmo_workflow.adapters.biliup_rejections import submit_rate_limited
from youmo_workflow.domain.batch_plan import BatchPlanUpdate
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.production_buffer import active_producer, buffer_used
from youmo_workflow.features.publication_cooldown import (
    MINIMUM_GAP,
    active_until,
    clear_rejections,
    current_state,
    pace_attempt,
    record_rejection,
)
from youmo_workflow.features.tasks import TaskService, add_task
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task

REJECTED = (
    'ResponseData { code: 137022, data: None, message: "投稿过于频繁，请稍后再试", ttl: Some(1) }'
)


@pytest.mark.parametrize("code", ["BILI_PREUPLOAD_RATE_LIMIT", "BILI_SUBMIT_RATE_LIMIT"])
def test_persisted_unlimited_rate_retry_and_hourly_cap(
    runtime: Runtime, clock: Clock, code: str
) -> None:
    with runtime.factory.begin() as db:
        row = add_task(db, "publish", "retry-indefinitely", stamp(clock()))
        row.max_attempts = 1
    tasks = TaskService(runtime.factory, clock)
    for attempt, minutes in enumerate((5, 10, 20, 30, 60, 60, 60, 60), 1):
        task = tasks.claim(("publish",))
        assert task and task.attempts == attempt
        with runtime.factory.begin() as db:
            until = record_rejection(db, clock(), "137022")
            assert until == stamp(clock() + timedelta(minutes=minutes))
        assert tasks.fail(task, WorkflowError(code, "explicit rejection", retryable=True))
        delay = max(MINIMUM_GAP, timedelta(minutes=minutes))
        with runtime.factory() as db:
            stored = db.get(Task, task.id)
            assert stored and stored.status == "retry_wait"
            assert stored.next_run_at == stamp(clock() + delay)
        tasks = TaskService(runtime.factory, clock)  # Restart does not reset streak or attempts.
        clock.at += delay - timedelta(seconds=1)
        assert tasks.claim(("publish",)) is None
        clock.at += timedelta(seconds=1)
    with runtime.factory.begin() as db:
        clear_rejections(db, clock())
        assert current_state(db, clock()).consecutive_rejections == 0
        assert active_until(db, clock()) == stamp(clock() + MINIMUM_GAP)
    clock.at += MINIMUM_GAP
    with runtime.factory.begin() as db:
        assert record_rejection(db, clock()) == stamp(clock() + timedelta(minutes=5))


def test_pacing_does_not_block_local_work_and_never_extends_on_idle_reads(
    runtime: Runtime, clock: Clock
) -> None:
    with runtime.factory.begin() as db:
        pace_attempt(db, clock())
        add_task(db, "publish", "delayed", stamp(clock()))
        add_task(db, "prepare_video", "local", stamp(clock()))
    task = runtime.tasks.claim()
    assert task and task.kind == "prepare_video"
    runtime.tasks.finish(task)
    assert runtime.tasks.claim() is None
    clock.at += MINIMUM_GAP
    task = runtime.tasks.claim()
    assert task and task.kind == "publish"


def test_full_1830_seconds_are_measured_after_upload_completion_and_survive_restart(
    runtime: Runtime, clock: Clock
) -> None:
    assert MINIMUM_GAP == timedelta(seconds=1830)
    with runtime.factory.begin() as db:
        pace_attempt(db, clock())
        add_task(db, "publish", "after-long-upload", stamp(clock()))
    clock.at += timedelta(minutes=10)
    with runtime.factory.begin() as db:
        clear_rejections(db, clock())
        expected = stamp(clock() + timedelta(seconds=1830))
        assert active_until(db, clock()) == expected
    clock.at += timedelta(seconds=1829)
    restarted = TaskService(runtime.factory, clock)
    assert restarted.claim(("publish",)) is None
    with runtime.factory() as db:
        assert current_state(db, clock()).next_submit_at == expected
    clock.at += timedelta(seconds=1)
    assert restarted.claim(("publish",)) is not None


def test_only_exact_explicit_rate_rejections_allow_retries() -> None:
    assert submit_rate_limited(1, REJECTED, "Error: " + REJECTED)
    for stdout in (
        REJECTED.replace("137022", "-663"),
        REJECTED.replace("None", "Some(Object {})"),
        REJECTED + " BV1234567890",
        REJECTED + ' ResponseData { code: 0, data: None, message: "OK" }',
        "HTTP 412",
        "timeout",
        "请输入验证码信息",
    ):
        assert not submit_rate_limited(1, stdout, "")
    assert not submit_rate_limited(0, REJECTED, "")


def configured(runtime: Runtime, clock: Clock) -> int:
    runtime.sources.ingest("tbsnewsdig", discovery_items(600), stamp(clock()))
    service = BatchPlanService(runtime.factory, runtime.policy, clock)
    return service.update(
        BatchPlanUpdate(
            revision=0,
            batch_size=100,
            publications_per_batch=10,
            auto_enabled=True,
            confirm_execution=True,
        )
    ).revision


def produced_waiting(runtime: Runtime, clock: Clock, batch_id: str) -> None:
    with runtime.factory.begin() as db:
        for task in db.scalars(select(Task).where(Task.batch_id == batch_id)):
            task.status = "succeeded"
        waiting = add_task(db, "publish", f"waiting:{batch_id}", stamp(clock()))
        waiting.batch_id, waiting.status = batch_id, "retry_wait"
        waiting.next_run_at = stamp(clock() + timedelta(hours=1))


def test_fifty_reserved_slots_block_next_batch_while_consumer_waits(
    runtime: Runtime, clock: Clock
) -> None:
    revision = configured(runtime, clock)
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock)
    for count in range(1, 6):
        batch = trigger.trigger()
        assert batch and batch.candidate_count == 100
        produced_waiting(runtime, clock, batch.id)
        with runtime.factory() as db:
            assert active_producer(db) is None
            assert buffer_used(db) == count * 10
    assert trigger.trigger() is None
    with pytest.raises(WorkflowError) as error:
        trigger.trigger(manual(revision))
    assert error.value.code == "PRODUCTION_BUFFER_FULL"
    state = BatchPlanService(runtime.factory, runtime.policy, clock).state()
    assert state.buffer_used == state.buffer_limit == 50 and not state.automatic_blocked


def test_racing_producers_cannot_overreserve_last_ten_slots(runtime: Runtime, clock: Clock) -> None:
    revision = configured(runtime, clock)
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock)
    for _ in range(4):
        batch = trigger.trigger()
        assert batch
        produced_waiting(runtime, clock, batch.id)

    def attempt(_index: int) -> bool:
        try:
            return trigger.trigger(manual(revision)) is not None
        except WorkflowError as error:
            assert error.code in {"BATCH_ALREADY_ACTIVE", "PRODUCTION_BUFFER_FULL"}
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, range(2))) == 1
    with runtime.factory() as db:
        assert buffer_used(db) == 50
