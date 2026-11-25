from datetime import timedelta

import pytest
from batch_pipeline_support import RecordingGateway, batch_runner, offline_live_assets
from conftest import Assets, Clock
from selection_support import discovery_items
from sqlalchemy import select
from test_batch_trigger import configure, manual

from youmo_workflow.domain.batch_plan import ProductionDispatch
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.initial_selection import selection_results
from youmo_workflow.features.publication_cooldown import MINIMUM_GAP
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task


def test_full_threshold_pipeline_posts_exact_count_and_preserves_top30(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    revision = configure(runtime, clock, count=2)
    runtime.sources.ingest("tbsnewsdig", discovery_items(55), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(revision))
    assert batch
    gateway = RecordingGateway()
    runner = batch_runner(runtime, clock, offline_live_assets(runtime, assets), gateway)
    configure(runtime, clock, count=5)
    for _ in range(25):
        if not runner.once():
            clock.at += MINIMUM_GAP
    assert runtime.batches.detail(batch.id).status == "completed", runtime.tasks.list_tasks()
    result = selection_results(runtime.factory, batch.id)
    assert result.initial == result.rescreen and result.initial
    assert len(result.initial.shortlisted_ids) == 30
    with runtime.factory() as db:
        task = db.scalar(select(Task).where(Task.kind == "dispatch_production"))
        assert task and task.output_json
        dispatch = ProductionDispatch.model_validate_json(task.output_json)
        assert dispatch.video_ids == result.initial.shortlisted_ids[:2]
        assert len(list(db.scalars(select(Task).where(Task.kind == "prepare_video")))) == 2
    assert len(gateway.uploads) == 2 and gateway.writes == 4
    assert len(runtime.publications.list_publications()) == 2
    assert all(
        row.status == "completed" and row.remote_id
        for row in runtime.publications.list_publications()
    )
    assert not runtime.settings.publication_enabled and not runtime.settings.automation_enabled
    assert not runner.once() and gateway.writes == 4


def test_test_artifacts_cannot_be_submitted_even_with_batch_authorization(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    revision = configure(runtime, clock, count=1)
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(revision))
    assert batch
    gateway = RecordingGateway()
    runner = batch_runner(runtime, clock, assets, gateway)
    for _ in range(15):
        if not runner.once():
            break
    assert gateway.writes == 0
    assert runtime.batches.detail(batch.id).error_code == "PRIVATE_UPLOAD_NOT_ELIGIBLE"


def test_publish_timeout_never_reuploads_and_generic_retry_is_rejected(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    revision = configure(runtime, clock, count=1)
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(revision))
    gateway = RecordingGateway()
    runner = batch_runner(runtime, clock, offline_live_assets(runtime, assets), gateway)
    while True:
        with runtime.factory() as db:
            pending = db.scalar(select(Task).where(Task.kind == "publish"))
        if pending:
            break
        assert runner.once()
    task = runtime.tasks.claim()
    assert task and task.kind == "publish"
    clock.at += timedelta(minutes=121)
    assert runtime.tasks.recover() == 1
    with pytest.raises(WorkflowError, match="重试"):
        runtime.tasks.retry(task.id)
    assert not runner.once() and gateway.writes == 0
