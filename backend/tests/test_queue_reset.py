import json
from datetime import timedelta

import pytest
from conftest import Assets, Clock, sample_video
from sqlalchemy import select
from test_publications import manifest

from youmo_workflow.domain.batch_plan import BatchExecution, BatchPlan
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.batch_progress import refresh_batch_progress
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.batches import eligible_query
from youmo_workflow.features.production_buffer import buffer_used
from youmo_workflow.orchestration.backlog_control import BacklogController, BacklogRun, FrozenSource
from youmo_workflow.orchestration.queue_reset import clear_queue
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Publication, Task, WorkflowSetting


def new_plan() -> BatchPlan:
    return BatchPlan(
        batch_size=50,
        publications_per_batch=2,
        auto_enabled=True,
        publication_mode="public_reprint",
    )


def test_cutoff_excludes_late_discovered_old_sources_and_refills_fifty_two(
    runtime: Runtime, clock: Clock
) -> None:
    cutoff = clock()
    clear_queue(runtime, cutoff, new_plan(), "User explicitly starts fresh")
    clock.at += timedelta(minutes=1)
    old = sample_video(clock)
    equal = sample_video(clock, "equaltime01").model_copy(update={"published_at": stamp(cutoff)})
    fresh = [sample_video(clock, f"{i:011d}", hours=0) for i in range(50)]
    runtime.sources.ingest("tbsnewsdig", [old, equal, *fresh[:49]], stamp(clock()))
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock)
    assert trigger.trigger() is None
    runtime.sources.ingest("tbsnewsdig", [fresh[-1]], stamp(clock()))
    batch = trigger.trigger()
    assert batch and batch.candidate_count == 50 and batch.publications_per_batch == 2
    assert {row.video_id for row in runtime.batches.detail(batch.id).items} == {
        row.video_id for row in fresh
    }
    with runtime.factory() as db:
        assert not list(db.scalars(eligible_query(clock(), runtime.policy)))


def seed_queue(runtime: Runtime, clock: Clock, assets: Assets) -> str:
    at = stamp(clock())
    execution = BatchExecution(
        batch_size=100,
        publications_per_batch=10,
        plan_revision=1,
        trigger="manual",
        producer_consumer=True,
    ).model_dump_json()
    with runtime.factory.begin() as db:
        db.add(
            Batch(
                id="old",
                local_date="2026-11-06",
                snapshot_at=at,
                status="running",
                policy_json=runtime.policy.model_dump_json(),
                candidate_count=100,
                execution_json=execution,
            )
        )
        for identifier, status, remote in (
            ("queued", "queued", None),
            ("unknown", "unknown", None),
            ("delivered", "uploaded", "BV1234567890"),
        ):
            payload = (
                manifest(assets, clock)
                .model_copy(
                    update={
                        "content_id": identifier,
                        "test_only": False,
                    }
                )
                .model_dump_json()
            )
            db.add(
                Publication(
                    id=identifier,
                    content_id=identifier,
                    manifest_json=payload,
                    manifest_hash="a" * 64,
                    confirmed_hash="a" * 64,
                    status=status,
                    remote_id=remote,
                    created_at=at,
                )
            )
        request = FinalizePrivate(publication_id="queued", manifest_hash="a" * 64).model_dump_json()
        db.add(
            Task(
                id="waiting",
                unique_key="batch-publish:queued",
                batch_id="old",
                kind="publish",
                status="retry_wait",
                attempts=7,
                max_attempts=1,
                input_json=request,
                error_code="BILI_SUBMIT_RATE_LIMIT",
                created_at=at,
                updated_at=at,
                next_run_at=at,
            )
        )
        run = BacklogRun(
            sources=[FrozenSource(relative_path="data/backlog/old.json", sha256="a" * 64)],
            authorized_at=at,
            authority="previous scope",
            status="attention_required",
            enabled=False,
        )
        db.add(
            WorkflowSetting(id="frozen_backlog_run", value_json=run.model_dump_json(), revision=1)
        )
    return execution


def test_clear_cancels_only_undelivered_queue_and_preserves_receipts_media_and_history(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    execution = seed_queue(runtime, clock, assets)
    media = runtime.artifacts.resolve(assets.media, "media")
    before = media.read_bytes()
    result = clear_queue(runtime, clock(), new_plan(), "User cleared queue")
    assert result == {
        "cancelled_tasks": 1,
        "cancelled_publications": 1,
        "cancelled_batches": 1,
        "files_deleted": 0,
    }
    refresh_batch_progress(runtime.factory, "old", clock())
    with runtime.factory() as db:
        task, batch = db.get(Task, "waiting"), db.get(Batch, "old")
        queued, unknown, delivered = (
            db.get(Publication, key) for key in ("queued", "unknown", "delivered")
        )
        assert task and task.status == "cancelled" and task.attempts == 7 and task.max_attempts == 1
        assert batch and batch.status == "cancelled" and batch.execution_json == execution
        assert queued and queued.status == "cancelled" and queued.manifest_hash == "a" * 64
        assert unknown and unknown.status == "unknown" and unknown.remote_id is None
        assert (
            delivered and delivered.remote_id == "BV1234567890" and delivered.status == "uploaded"
        )
        assert buffer_used(db) == 0  # Cancelled and unknown packages stay archived, not reserved.
        saved = db.scalar(
            select(WorkflowSetting).where(WorkflowSetting.id.startswith("queue-reset:"))
        )
        assert saved and json.loads(saved.value_json)["tasks"][0]["attempts"] == 7
    assert media.read_bytes() == before
    with pytest.raises(WorkflowError, match="此时间边界已处理"):
        clear_queue(runtime, clock(), new_plan(), "same operation")
    with pytest.raises(WorkflowError, match="用户已清空"):
        BacklogController(runtime).resume_after_diagnosis({}, True)


def test_inflight_upload_blocks_reset_without_altering_state(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    seed_queue(runtime, clock, assets)
    with runtime.factory.begin() as db:
        task = db.get(Task, "waiting")
        assert task
        task.status = "running"
    with pytest.raises(WorkflowError, match="在途"):
        clear_queue(runtime, clock(), new_plan(), "clear requested")
    with runtime.factory() as db:
        assert db.get(WorkflowSetting, "queue_cutoff") is None
        task = db.get(Task, "waiting")
        assert task and task.status == "running"


def test_historical_failure_does_not_block_new_cutoff_scope(runtime: Runtime, clock: Clock) -> None:
    at = stamp(clock() - timedelta(seconds=1))
    with runtime.factory.begin() as db:
        execution = BatchExecution(
            batch_size=100, publications_per_batch=10, plan_revision=1, trigger="manual"
        ).model_dump_json()
        db.add(
            Batch(
                id="failed",
                local_date="2026-11-06",
                snapshot_at=at,
                status="failed",
                policy_json=runtime.policy.model_dump_json(),
                candidate_count=100,
                execution_json=execution,
            )
        )
    clear_queue(runtime, clock(), new_plan(), "user cleared historical queue")
    state = BatchPlanService(runtime.factory, runtime.policy, clock).state()
    assert (
        not state.automatic_blocked and state.batch_size == 50 and state.publications_per_batch == 2
    )
