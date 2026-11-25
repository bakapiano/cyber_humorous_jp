from datetime import timedelta

import pytest
from batch_pipeline_support import RecordingGateway, batch_runner, offline_live_assets
from conftest import Assets, Clock
from selection_support import discovery_items
from sqlalchemy import select
from test_batch_trigger import configure, manual
from test_pipeline import FixtureEditing

from youmo_workflow.domain.editing import EditingOutput
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.pipeline import PRODUCTION_KINDS, SELECTION_KINDS, stage_assignments
from youmo_workflow.domain.providers import ReadyContent
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.production_buffer import buffer_used
from youmo_workflow.features.tasks import add_task
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task


@pytest.mark.parametrize("count", range(1, 9))
def test_pool_ownership_covers_every_producer_stage(count: int) -> None:
    groups = stage_assignments(count)
    assert len(groups) == count
    assert set(sum(groups, ())) == set(PRODUCTION_KINDS)
    assert not set(sum(groups, ())) & {"publish", "sync_source", "cleanup_publication"}
    if count >= 4:
        assert groups[0] == SELECTION_KINDS
        assert all(not set(group) & set(SELECTION_KINDS) for group in groups[1:])


def test_one_queue_cannot_starve_other_stage_consumers(runtime: Runtime, clock: Clock) -> None:
    with runtime.factory.begin() as db:
        for index in range(10):
            add_task(db, "prepare_video", f"source-{index}", stamp(clock()))
        for kind in ("initial_selection", "packaging", "editing", "publish"):
            add_task(db, kind, kind, stamp(clock()))
    running = [runtime.tasks.claim(group) for group in stage_assignments(4)]
    assert {task.kind for task in running if task} == {
        "initial_selection",
        "prepare_video",
        "packaging",
        "editing",
    }
    assert runtime.tasks.claim(("publish",)) is not None


class OneBadVideo(FixtureEditing):
    calls = 0

    def edit(self, request: ReadyContent) -> EditingOutput:
        self.calls += 1
        if self.calls == 1:
            raise WorkflowError(
                "MODEL_FORMAT_RETRIES_EXHAUSTED", "Synthetic exhausted model output"
            )
        return super().edit(request)


def test_failed_video_does_not_stop_sibling_upload_cleanup_or_next_batch(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    revision = configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(100), stamp(clock()))
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock, lambda: False)
    first = trigger.trigger(manual(revision))
    assert first  # Publisher login is not producer admission.
    live = offline_live_assets(runtime, assets)
    gateway = RecordingGateway()
    runner = batch_runner(runtime, clock, live, gateway)
    runner.providers.editor = OneBadVideo(runtime, live)
    for _ in range(35):
        if not runner.once():
            break
    assert len(gateway.uploads) == 1
    state = BatchPlanService(runtime.factory, runtime.policy, clock).state()
    assert not state.automatic_blocked and state.eligible_count == 50 and state.buffer_used == 1
    with runtime.factory() as db:
        failed = db.scalar(select(Task).where(Task.batch_id == first.id, Task.status == "failed"))
        assert failed and failed.kind == "editing" and failed.attempts == 1
        assert failed.error_code == "MODEL_FORMAT_RETRIES_EXHAUSTED"
        stored = db.get(Batch, first.id)
        assert stored and stored.status == "failed"
    second = trigger.trigger()
    assert second and second.id != first.id
    assert set(item.video_id for item in runtime.batches.detail(first.id).items).isdisjoint(
        item.video_id for item in runtime.batches.detail(second.id).items
    )


def test_undispatched_failed_selection_releases_slots_not_candidates(
    runtime: Runtime, clock: Clock
) -> None:
    configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(100), stamp(clock()))
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock)
    first = trigger.trigger()
    assert first
    task = runtime.tasks.claim(SELECTION_KINDS)
    assert task
    runtime.tasks.fail(task, WorkflowError("MODEL_FORMAT_RETRIES_EXHAUSTED", "synthetic"))
    with runtime.factory() as db:
        assert buffer_used(db) == 0
    second = trigger.trigger()
    assert second and second.id != first.id
    assert len(runtime.batches.detail(first.id).items) == 50


def test_selection_cannot_reserve_beyond_fifty_while_editing_is_busy(
    runtime: Runtime, clock: Clock
) -> None:
    configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(1400), stamp(clock()))
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock)
    for _ in range(25):
        assert trigger.trigger() is not None
    assert trigger.trigger() is None
    with runtime.factory() as db:
        assert buffer_used(db) == 50
    assert BatchPlanService(runtime.factory, runtime.policy, clock).state().eligible_count == 150


def test_explicit_retry_preserves_attempts_and_ignores_other_batch_work(
    runtime: Runtime, clock: Clock
) -> None:
    configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(100), stamp(clock()))
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock)
    assert trigger.trigger()
    task = runtime.tasks.claim(SELECTION_KINDS)
    assert task
    runtime.tasks.fail(task, WorkflowError("MODEL_FORMAT_RETRIES_EXHAUSTED", "synthetic"))
    clock.at += timedelta(seconds=1)
    assert trigger.trigger()
    with runtime.factory.begin() as db:
        row = db.get(Task, task.id)
        assert row
        row.max_attempts = 2  # Synthetic authorized task budget; not a runtime reset.
    runtime.tasks.retry(task.id)
    with runtime.factory() as db:
        row = db.get(Task, task.id)
        assert row and row.attempts == 1 and row.error_code == "MODEL_FORMAT_RETRIES_EXHAUSTED"
    clock.at += timedelta(seconds=1)
    retried = runtime.tasks.claim(SELECTION_KINDS)
    assert retried and retried.id == task.id and retried.attempts == 2
