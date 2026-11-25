import pytest
from conftest import Assets, Clock, sample_video
from selection_support import discovery_items
from test_batch_trigger import configure
from test_publications import manifest

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.providers import SelectedContent
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.production_buffer import buffer_used
from youmo_workflow.features.tasks import add_task
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Publication, Task


def store_package(assets: Assets, clock: Clock, status: str) -> Publication:
    payload = manifest(assets, clock).model_copy(update={"test_only": False})
    return Publication(
        id="post",
        content_id=payload.content_id,
        status=status,
        manifest_json=payload.model_dump_json(),
        manifest_hash="a" * 64,
        created_at=stamp(clock()),
    )


@pytest.mark.parametrize("status", ["failed", "unknown", "retry_wait"])
@pytest.mark.parametrize("bound", [False, True])
def test_upload_slot_releases_only_terminal_outcome(
    runtime: Runtime, clock: Clock, assets: Assets, status: str, bound: bool
) -> None:
    configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger() if bound else None
    assert (batch is not None) == bound
    package_status = "unknown" if status == "unknown" else "queued"
    with runtime.factory.begin() as db:
        package = store_package(assets, clock, package_status)
        db.add(package)
        upload = add_task(db, "publish", "batch-publish:post", stamp(clock()))
        upload.batch_id, upload.status, upload.attempts = batch.id if batch else None, status, 1
        upload.input_json = FinalizePrivate(
            publication_id=package.id, manifest_hash=package.manifest_hash
        ).model_dump_json()
    with runtime.factory() as db:
        assert buffer_used(db) == int(bound) + int(status == "retry_wait")
        assert db.get(Task, upload.id) is not None
        assert runtime.publications.require(db, "post").status == package_status
    if status != "retry_wait":
        assert runtime.tasks.claim(("publish",)) is None


def test_same_failed_video_releases_one_slot_and_retry_reacquires_it(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(1350), stamp(clock()))
    trigger = BatchTriggerService(runtime.factory, runtime.policy, clock)
    first = trigger.trigger()
    assert first
    content = SelectedContent(
        content_id="failed-content",
        video_id=sample_video(clock).video_id,
        original_title="fixture",
        original_media_ref=assets.media,
        transcript_ref=assets.subtitle,
        translation_ref=assets.subtitle,
        evidence=[],
        expires_at=stamp(clock()),
    )
    with runtime.factory.begin() as db:
        for key in ("packaging:failed-content", "packaging:redundant-record"):
            row = add_task(db, "packaging", key, stamp(clock()))
            row.batch_id, row.status, row.attempts = first.id, "failed", 1
            row.input_json = content.model_dump_json()
        first_row = db.get(Batch, first.id)
        assert first_row is not None
        first_row.status = "failed"
    for _ in range(24):
        assert trigger.trigger() is not None
    assert trigger.trigger() is None
    with runtime.factory() as db:
        assert buffer_used(db) == 49
    queued = store_package(assets, clock, "queued")
    with runtime.factory.begin() as db:
        db.add(queued)
    with pytest.raises(WorkflowError) as rejected:
        runtime.tasks.retry(row.id)
    assert rejected.value.code == "PRODUCTION_BUFFER_FULL"
    with runtime.factory.begin() as db:
        saved = db.get(Task, row.id)
        assert saved and saved.status == "failed" and saved.attempts == 1
        runtime.publications.require(db, queued.id).status = "cancelled"
    assert runtime.tasks.retry(row.id).status == "pending"
    with runtime.factory() as db:
        assert buffer_used(db) == 50
    assert trigger.trigger() is None
