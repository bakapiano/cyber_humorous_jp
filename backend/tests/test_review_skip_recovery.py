import json

import pytest
from conftest import Assets, Clock
from selection_support import discovery_items
from sqlalchemy import select
from test_batch_trigger import configure, manual
from test_review_skips import ReviewOptions, run_batch

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.providers import ReadyContent
from youmo_workflow.domain.review_skip import SupplementalReviewSkip
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_progress import refresh_batch_progress
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.review_skip_scope import authorized_review_skip, digest
from youmo_workflow.features.review_skips import mark_review_skip, verified_review_skips
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting


@pytest.mark.parametrize("automatic", [False, True])
def test_quantity_trigger_retains_review_skip_policy(
    runtime: Runtime, clock: Clock, automatic: bool
) -> None:
    revision = configure(runtime, clock, auto=automatic)
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(
        None if automatic else manual(revision)
    )
    assert batch
    with runtime.factory() as db:
        row = db.get(Batch, batch.id)
        assert row and row.execution_json
        assert BatchExecution.model_validate_json(row.execution_json).skip_review_rejections


@pytest.mark.parametrize("tamper", [None, "execution", "input", "output", "attempts", "cancelled"])
def test_supplemental_skip_preserves_frozen_execution_and_attempts(
    runtime: Runtime, clock: Clock, assets: Assets, tamper: str | None
) -> None:
    identifier, gateway = run_batch(runtime, clock, assets, ReviewOptions(enabled=False))
    with runtime.factory.begin() as db:
        batch = db.get(Batch, identifier)
        task = db.scalar(select(Task).where(Task.batch_id == identifier, Task.status == "failed"))
        assert batch and batch.execution_json and task
        original = (batch.execution_json, task.input_json, task.output_json, task.attempts)
        content = ReadyContent.model_validate_json(task.input_json).content
        record = SupplementalReviewSkip(
            task_id=task.id,
            batch_id=batch.id,
            video_id=content.video_id,
            content_id=content.content_id,
            error_code=task.error_code or "",
            input_sha256=digest(task.input_json),
            output_sha256=digest(task.output_json),
            execution_sha256=digest(batch.execution_json),
            attempts=task.attempts,
            authorized_at=stamp(clock()),
        )
        db.add(
            WorkflowSetting(
                id="review_skip_authorization:" + task.id,
                revision=1,
                value_json=record.model_dump_json(),
            )
        )
        db.flush()
        if tamper == "execution":
            batch.execution_json = json.dumps(
                {**json.loads(batch.execution_json), "plan_revision": 99}
            )
        elif tamper == "input":
            task.input_json += " "
        elif tamper == "output":
            task.output_json = "changed"
        elif tamper == "attempts":
            task.attempts += 1
        elif tamper == "cancelled":
            batch.status = "cancelled"
        assert authorized_review_skip(db, task, content.content_id) == (tamper is None)
        if tamper:
            return
        mark_review_skip(db, task, clock())
        assert task.status == "skipped"
        assert (batch.execution_json, task.input_json, task.output_json, task.attempts) == original
        assert verified_review_skips(db, identifier, [task]) == {content.video_id}
    refresh_batch_progress(runtime.factory, identifier, clock())
    with runtime.factory() as db:
        batch = db.get(Batch, identifier)
        assert batch and batch.status == "cleaning"
    assert len(gateway.uploads) == 1
