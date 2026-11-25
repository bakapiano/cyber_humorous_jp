import json
from dataclasses import dataclass

import pytest
from batch_pipeline_support import RecordingGateway, batch_runner, offline_live_assets
from conftest import Assets, Clock
from selection_support import discovery_items
from sqlalchemy import select
from test_batch_trigger import configure, manual
from test_pipeline import FixtureEditing

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.editing import EditingOutput
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import ReadyContent, SelectedContent
from youmo_workflow.domain.review_skip import ReviewSkip
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_progress import refresh_batch_progress
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.review_skips import PREFIX, verified_review_skips
from youmo_workflow.orchestration.cleanup import BatchMediaCleanup
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting


class RejectedEditor(FixtureEditing):
    code = "SUBTITLE_LLM_REVIEW_BLOCKED"
    reject_all = False
    calls = 0

    def edit(self, request: ReadyContent) -> EditingOutput:
        self.calls += 1
        if self.reject_all or self.calls == 1:
            raise WorkflowError(self.code, "Synthetic review refusal; no real provider")
        return super().edit(request)


@dataclass
class ReviewOptions:
    enabled: bool = True
    code: str = "SUBTITLE_LLM_REVIEW_BLOCKED"
    reject_all: bool = False


def run_batch(
    runtime: Runtime, clock: Clock, assets: Assets, options: ReviewOptions | None = None
) -> tuple[str, RecordingGateway]:
    options = options or ReviewOptions()
    revision = configure(runtime, clock, count=2)
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(revision))
    assert batch
    with runtime.factory.begin() as db:
        row = db.get(Batch, batch.id)
        assert row and row.execution_json
        execution = BatchExecution.model_validate_json(row.execution_json)
        row.execution_json = execution.model_copy(
            update={"skip_review_rejections": options.enabled}
        ).model_dump_json()
    live_assets = offline_live_assets(runtime, assets)
    gateway = RecordingGateway()
    runner = batch_runner(runtime, clock, live_assets, gateway)
    editor = RejectedEditor(runtime, live_assets)
    editor.code = options.code
    editor.reject_all = options.reject_all
    runner.providers.editor = editor
    for _ in range(35):
        with runtime.factory() as db:
            if db.scalar(select(Task.id).where(Task.kind == "cleanup_batch")):
                break
        if not runner.once():
            break
    return batch.id, gateway


def test_all_rejected_items_are_terminal_without_upload_or_media_cleanup(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    identifier, gateway = run_batch(runtime, clock, assets, ReviewOptions(reject_all=True))
    cleanup = runtime.tasks.claim(("cleanup_batch",))
    assert cleanup and not gateway.uploads
    assert BatchMediaCleanup(runtime).verified_contents(cleanup) == []
    result = BatchMediaCleanup(runtime).run(cleanup)
    assert json.loads(result)["video_files"] == 0
    runtime.tasks.finish(cleanup, result)
    refresh_batch_progress(runtime.factory, identifier, clock())
    with runtime.factory() as db:
        batch = db.get(Batch, identifier)
        assert batch and batch.status == "completed_with_skips"


def test_review_skip_is_not_success_and_cleanup_preserves_rejected_video(
    runtime: Runtime,
    clock: Clock,
    assets: Assets,
) -> None:
    identifier, gateway = run_batch(runtime, clock, assets)
    assert len(gateway.uploads) == 1
    with runtime.factory() as db:
        tasks = list(db.scalars(select(Task).where(Task.batch_id == identifier)))
        skipped = verified_review_skips(db, identifier, tasks)
        assert len(skipped) == 1
        rejected = next(task for task in tasks if task.status == "skipped")
        assert rejected.error_code == "SUBTITLE_LLM_REVIEW_BLOCKED" and rejected.attempts == 1
        setting = db.get(WorkflowSetting, PREFIX + rejected.id)
        assert (
            setting and not ReviewSkip.model_validate_json(setting.value_json).publication_succeeded
        )
        contents = [
            SelectedContent.model_validate_json(task.output_json or "{}")
            for task in tasks
            if task.kind == "prepare_video"
        ]
    for content in contents:
        directory = runtime.artifacts.root / "production" / content.content_id
        directory.mkdir(parents=True)
        (directory / "original.mp4").write_bytes(b"synthetic")
        (directory / "request.json").write_text("preserved", encoding="utf-8")
    cleanup = runtime.tasks.claim(("cleanup_batch",))
    assert cleanup
    result = BatchMediaCleanup(runtime).run(cleanup)
    assert json.loads(result)["video_files"] == 1
    assert runtime.tasks.finish(cleanup, result)
    refresh_batch_progress(runtime.factory, identifier, clock())
    for content in contents:
        directory = runtime.artifacts.root / "production" / content.content_id
        assert (directory / "original.mp4").exists() == (content.video_id in skipped)
        assert (directory / "request.json").read_text(encoding="utf-8") == "preserved"
    with runtime.factory() as db:
        batch = db.get(Batch, identifier)
        assert batch and batch.status == "completed_with_skips"
    with pytest.raises(WorkflowError, match="只允许"):
        runtime.tasks.retry(rejected.id)


@pytest.mark.parametrize(
    "code", ["SUBMIT_RESULT_UNKNOWN", "MODEL_FORMAT_RETRIES_EXHAUSTED", "REMOTE_LOOKUP_FAILED"]
)
def test_non_review_failures_are_never_skipped(
    runtime: Runtime, clock: Clock, assets: Assets, code: str
) -> None:
    identifier, _ = run_batch(runtime, clock, assets, ReviewOptions(code=code))
    with runtime.factory() as db:
        tasks = list(db.scalars(select(Task).where(Task.batch_id == identifier)))
        assert not verified_review_skips(db, identifier, tasks)
        assert not any(task.kind == "cleanup_batch" or task.status == "skipped" for task in tasks)
        batch = db.get(Batch, identifier)
        assert batch and batch.status == "failed"


def test_review_skip_requires_frozen_batch_authorization(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    identifier, _ = run_batch(runtime, clock, assets, ReviewOptions(enabled=False))
    with runtime.factory() as db:
        tasks = list(db.scalars(select(Task).where(Task.batch_id == identifier)))
        assert not any(task.status == "skipped" or task.kind == "cleanup_batch" for task in tasks)
        batch = db.get(Batch, identifier)
        assert batch and batch.execution_json
        assert "skip_review_rejections" not in batch.execution_json


def test_forged_skip_cannot_complete_or_clean_batch(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    identifier, _ = run_batch(runtime, clock, assets)
    with runtime.factory.begin() as db:
        task = db.scalar(select(Task).where(Task.batch_id == identifier, Task.status == "skipped"))
        assert task
        task.error_code = "SUBMIT_RESULT_UNKNOWN"
    with pytest.raises(WorkflowError) as error:
        refresh_batch_progress(runtime.factory, identifier, clock())
    assert error.value.code == "REVIEW_SKIP_UNVERIFIED"
    cleanup = runtime.tasks.claim(("cleanup_batch",))
    assert cleanup
    with pytest.raises(WorkflowError) as cleanup_error:
        BatchMediaCleanup(runtime).run(cleanup)
    assert cleanup_error.value.code == "REVIEW_SKIP_UNVERIFIED"
