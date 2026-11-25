import pytest
from conftest import Assets, Clock
from sqlalchemy import select
from test_publications import manifest

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.providers import SelectedContent
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_task_view import effective_batch_tasks
from youmo_workflow.features.publication_replacements import stage_replacement
from youmo_workflow.features.tasks import add_task
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task


def withdrawn(runtime: Runtime, assets: Assets, clock: Clock) -> str:
    value = manifest(assets, clock)
    value.visibility = "public"
    publication = runtime.publications.prepare(value)
    runtime.publications.confirm(publication.id, publication.manifest_hash)
    content = SelectedContent(
        content_id=value.content_id,
        video_id="abcdefghijk",
        original_title="synthetic",
        original_media_ref=assets.media,
        transcript_ref=assets.media,
        translation_ref=assets.subtitle,
        evidence=[],
        expires_at=value.expires_at,
    )
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, publication.id)
        row.status, row.remote_id = "withdrawn", "BV1234567890"
        source = add_task(db, "prepare_video", "synthetic-source", stamp(clock()))
        source.status, source.output_json, source.batch_id = (
            "succeeded",
            content.model_dump_json(),
            "synthetic-batch",
        )
        for kind, key in [
            ("publish", f"batch-publish:{row.id}"),
            ("finalize_private", f"finalize:{row.id}:{row.manifest_hash}"),
        ]:
            task = add_task(db, kind, key, stamp(clock()))
            task.input_json = FinalizePrivate(
                publication_id=row.id, manifest_hash=row.manifest_hash
            ).model_dump_json()
            task.status, task.batch_id, task.output_json = (
                "failed",
                source.batch_id,
                '{"prior_attempt":true}',
            )
    return publication.id


def test_replacement_is_idempotent_preserves_history_and_has_distinct_content(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    identifier = withdrawn(runtime, assets, clock)
    with runtime.factory() as db:
        before = runtime.publications.require(db, identifier).manifest_json
    result = stage_replacement(runtime.factory, identifier, "latest-v1", clock())
    assert stage_replacement(runtime.factory, identifier, "latest-v1", clock()) == result
    assert result.new_content_id != result.old_content_id
    with runtime.factory() as db:
        assert runtime.publications.require(db, identifier).manifest_json == before
        active = effective_batch_tasks(db, "synthetic-batch")
        assert {row.kind for row in active} == {"prepare_video", "packaging"}
        assert next(row for row in active if row.kind == "packaging").status == "pending"
        for task_id in result.superseded_task_ids:
            task = db.get(Task, task_id)
            assert (
                task
                and task.status == "superseded"
                and task.output_json == '{"prior_attempt":true}'
            )
    assert runtime.publications.list_publications()[0].visibility == "private"


def test_replacement_requires_withdrawal(runtime: Runtime, assets: Assets, clock: Clock) -> None:
    identifier = withdrawn(runtime, assets, clock)
    with runtime.factory.begin() as db:
        runtime.publications.require(db, identifier).status = "uploaded"
    with pytest.raises(WorkflowError, match="尚未确认下架"):
        stage_replacement(runtime.factory, identifier, "latest-v1", clock())


def test_missing_replacement_cannot_skip_superseded_work(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    identifier = withdrawn(runtime, assets, clock)
    record = stage_replacement(runtime.factory, identifier, "latest-v1", clock())
    with runtime.factory.begin() as db:
        task = db.get(Task, record.packaging_task_id)
        assert task
        db.delete(task)
    with runtime.factory() as db, pytest.raises(WorkflowError, match="链路不完整"):
        effective_batch_tasks(db, "synthetic-batch")


def test_running_old_task_cannot_be_superseded(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    identifier = withdrawn(runtime, assets, clock)
    with runtime.factory.begin() as db:
        task = db.scalar(select(Task).where(Task.kind == "publish"))
        assert task
        task.status = "running"
    with pytest.raises(WorkflowError, match="仍在执行"):
        stage_replacement(runtime.factory, identifier, "latest-v1", clock())
    with runtime.factory() as db:
        assert not db.scalar(select(Task.id).where(Task.kind == "packaging"))
