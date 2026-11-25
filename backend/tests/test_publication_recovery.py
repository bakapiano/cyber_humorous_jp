import json

import pytest
from conftest import Assets, Clock
from sqlalchemy import select
from test_publications import manifest

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.publication_recovery import resume_recovered_upload
from youmo_workflow.features.tasks import add_task
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task


def recovered(runtime: Runtime, assets: Assets, clock: Clock) -> FinalizePrivate:
    dto = runtime.publications.prepare(manifest(assets, clock))
    runtime.publications.confirm(dto.id, dto.manifest_hash)
    request = FinalizePrivate(publication_id=dto.id, manifest_hash=dto.manifest_hash)
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, dto.id)
        row.status, row.remote_id = "awaiting_subtitles", "BV1234567890"
        task = add_task(db, "publish", f"batch-publish:{dto.id}", stamp(clock()))
        task.input_json, task.status = request.model_dump_json(), "failed"
        task.error_code, task.attempts = "SUBMIT_RESULT_UNKNOWN", 1
    return request


def test_recovery_retains_failure_history_and_queues_subtitles_once(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    request = recovered(runtime, assets, clock)
    first = resume_recovered_upload(runtime.factory, request, "BV1234567890", clock())
    assert resume_recovered_upload(runtime.factory, request, "BV1234567890", clock()) == first
    with runtime.factory() as db:
        upload = db.scalar(select(Task).where(Task.kind == "publish"))
        finalizers = list(db.scalars(select(Task).where(Task.kind == "finalize_private")))
        assert upload and upload.status == "succeeded" and upload.attempts == 1
        record = json.loads(upload.output_json or "{}")
        assert record["recovery"]["previous_error_code"] == "SUBMIT_RESULT_UNKNOWN"
        assert record["recovery"]["upload_repeated"] is False
        assert len(finalizers) == 1 and finalizers[0].status == "pending"
        assert finalizers[0].input_json == request.model_dump_json()


@pytest.mark.parametrize("status", ["unknown", "submitting", "subtitle_unknown"])
def test_unverified_or_uncertain_writes_are_not_requeued(
    runtime: Runtime, assets: Assets, clock: Clock, status: str
) -> None:
    request = recovered(runtime, assets, clock)
    with runtime.factory.begin() as db:
        runtime.publications.require(db, request.publication_id).status = status
    with pytest.raises(WorkflowError, match="仅可续接"):
        resume_recovered_upload(runtime.factory, request, "BV1234567890", clock())
    with runtime.factory() as db:
        assert not db.scalar(select(Task.id).where(Task.kind == "finalize_private"))


def test_running_upload_is_not_claimed_by_recovery(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    request = recovered(runtime, assets, clock)
    with runtime.factory.begin() as db:
        row = db.scalar(select(Task).where(Task.kind == "publish"))
        assert row
        row.status = "running"
    with pytest.raises(WorkflowError, match="仍在执行"):
        resume_recovered_upload(runtime.factory, request, "BV1234567890", clock())


def test_changed_hash_rolls_back_recovery(runtime: Runtime, assets: Assets, clock: Clock) -> None:
    request = recovered(runtime, assets, clock).model_copy(update={"manifest_hash": "f" * 64})
    with pytest.raises(WorkflowError, match="仅可续接"):
        resume_recovered_upload(runtime.factory, request, "BV1234567890", clock())
    with runtime.factory() as db:
        upload = db.scalar(select(Task).where(Task.kind == "publish"))
        assert upload and upload.status == "failed"
