import pytest
from conftest import Assets, Clock
from sqlalchemy import func, select
from test_hard_subtitles import burned
from test_pipeline import FixtureEditing
from test_publications import manifest

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.hard_replacement import HardReplacementRequest
from youmo_workflow.domain.providers import PackagingOutput, ReadyContent, SelectedContent
from youmo_workflow.domain.remote import RemoteArchive
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.review_skips import mark_review_skip, verified_review_skips
from youmo_workflow.features.tasks import add_task
from youmo_workflow.features.video_review import record_video_rejection
from youmo_workflow.orchestration.hard_replacements import stage_hard_replacement
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task


def request_fixture(runtime: Runtime, assets: Assets, clock: Clock) -> HardReplacementRequest:
    old_manifest = manifest(assets, clock)
    old = runtime.publications.prepare(old_manifest)
    runtime.publications.confirm(old.id, old.manifest_hash)
    ready = ReadyContent(
        content=SelectedContent(
            content_id=old_manifest.content_id,
            video_id="abcdefghijk",
            original_title="synthetic",
            original_media_ref=assets.media,
            transcript_ref=assets.subtitle,
            translation_ref=assets.subtitle,
            evidence=[],
            expires_at=old_manifest.expires_at,
        ),
        packaging=PackagingOutput(
            content_id=old_manifest.content_id,
            title=old_manifest.title,
            cover_ref=assets.cover,
            original_media_ref=assets.media,
            packaging_version="raw_history_poster_v001",
        ),
    )
    hard = burned(runtime, assets, clock)
    editing = (
        FixtureEditing(runtime, assets)
        .edit(ready)
        .model_copy(
            update={
                "media_ref": hard.media_ref,
                "subtitle_ref": hard.subtitle_ref,
                "burn_in_ref": hard.burn_in_ref,
                "subtitle_mode": "hard",
                "version": "v004_hardsub",
            }
        )
    )
    with runtime.factory.begin() as db:
        publication = runtime.publications.require(db, old.id)
        publication.remote_id, publication.status = "BV1234567890", "subtitle_rejected"
        db.add(
            Batch(
                id="hard-replacement-batch",
                local_date="2026-11-06",
                snapshot_at=stamp(clock()),
                policy_json="{}",
                candidate_count=50,
                execution_json=BatchExecution(
                    batch_size=50, publications_per_batch=1, plan_revision=1, trigger="manual"
                ).model_dump_json(),
            )
        )
        task = add_task(db, "editing", "old-hard-source", stamp(clock()))
        task.batch_id, task.input_json, task.status = (
            "hard-replacement-batch",
            ready.model_dump_json(),
            "succeeded",
        )
        source_id = task.id
    return HardReplacementRequest(
        source_task_id=source_id,
        new_content_id="synthetic-hard-replacement",
        old_publication_ids=[old.id],
        editing=editing,
        owner_manages_removal=True,
    )


def test_owner_managed_removal_stages_distinct_upload_without_claiming_deletion(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    request = request_fixture(runtime, assets, clock)
    with runtime.factory() as db:
        old = runtime.publications.require(db, request.old_publication_ids[0])
        before = old.manifest_json
    record = stage_hard_replacement(runtime, request, clock())
    assert stage_hard_replacement(runtime, request, clock()) == record
    assert not record.deletion_verified and record.owner_manages_removal
    with runtime.factory() as db:
        old = runtime.publications.require(db, request.old_publication_ids[0])
        assert old.manifest_json == before and old.status == "subtitle_rejected"
        queued = db.get(Task, record.prepare_task_id)
        assert queued and queued.status == "pending" and queued.kind == "prepare_publication"
        assert (
            db.scalar(
                select(func.count()).select_from(Task).where(Task.kind == "prepare_publication")
            )
            == 1
        )
    changed = request.model_copy(
        update={"editing": request.editing.model_copy(update={"title": "changed"})}
    )
    with pytest.raises(WorkflowError, match="不同的替换"):
        stage_hard_replacement(runtime, changed, clock())


@pytest.mark.parametrize("problem", ["unknown", "authorization", "soft", "unbound_title"])
def test_replacement_does_not_bypass_identity_or_unknown_write_guards(
    runtime: Runtime, assets: Assets, clock: Clock, problem: str
) -> None:
    request = request_fixture(runtime, assets, clock)
    if problem == "unknown":
        with runtime.factory.begin() as db:
            old = runtime.publications.require(db, request.old_publication_ids[0])
            old.status, old.remote_id = "unknown", None
    elif problem == "authorization":
        request.owner_manages_removal = False
    else:
        request.editing = request.editing.model_copy(
            update={"subtitle_mode": "soft"}
            if problem == "soft"
            else {"title": "not the original title"}
        )
    with pytest.raises(WorkflowError):
        stage_hard_replacement(runtime, request, clock())
    with runtime.factory() as db:
        assert not db.scalar(select(Task.id).where(Task.kind == "prepare_publication"))


def test_replacement_refusal_scope_does_not_rewrite_old_batch_authorization(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    request = request_fixture(runtime, assets, clock)
    record = stage_hard_replacement(runtime, request, clock())
    value = manifest(assets, clock).model_copy(
        update={
            "content_id": request.new_content_id,
            "media_ref": request.editing.media_ref,
            "subtitle_ref": request.editing.subtitle_ref,
            "burn_in_ref": request.editing.burn_in_ref,
            "subtitle_mode": "hard",
        }
    )
    prepared = runtime.publications.prepare(value)
    runtime.publications.confirm(prepared.id, prepared.manifest_hash)
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, prepared.id)
        row.remote_id = "BV0000000001"
        task = add_task(db, "finalize_private", "new-refusal", stamp(clock()))
        task.batch_id, task.status, task.error_code = (
            record.batch_id,
            "failed",
            "VIDEO_REVIEW_REJECTED",
        )
        task.input_json = FinalizePrivate(
            publication_id=row.id, manifest_hash=row.manifest_hash
        ).model_dump_json()
        task_id = task.id
    record_video_rejection(
        runtime.factory,
        prepared.id,
        RemoteArchive(
            bvid="BV0000000001",
            title=value.title,
            aid=1,
            is_only_self=1,
            state=-2,
            state_desc="审核未通过",
        ),
        clock(),
    )
    with runtime.factory.begin() as db:
        task = db.get(Task, task_id)
        assert task
        mark_review_skip(db, task, clock())
        assert task.status == "skipped"
        assert verified_review_skips(db, record.batch_id, [task]) == {"abcdefghijk"}
        batch = db.get(Batch, record.batch_id)
        assert batch and batch.execution_json
        assert "skip_review_rejections" not in batch.execution_json
