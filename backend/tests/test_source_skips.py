from pathlib import Path

import pytest
from batch_pipeline_support import (
    FixtureSingleContent,
    RecordingGateway,
    batch_runner,
    offline_live_assets,
)
from conftest import Assets, Clock
from selection_support import discovery_items
from sqlalchemy import select
from test_batch_trigger import configure, manual

from youmo_workflow.adapters.acquisition import DownloadInfo, validate_download_identity
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.media_limits import SourceTooLong
from youmo_workflow.domain.models import VideoDTO
from youmo_workflow.domain.providers import SelectedContent
from youmo_workflow.domain.source_skip import SourceSkip
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.source_skips import PREFIX
from youmo_workflow.features.task_skips import verified_skips
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting


class LongContent(FixtureSingleContent):
    error: WorkflowError | None = None
    all_long = False

    def prepare_video(self, video: VideoDTO) -> SelectedContent:
        if self.all_long or not self.calls:
            self.calls.append(video.video_id)
            if self.error:
                raise self.error
            raise SourceTooLong(video.video_id, 12760)
        return super().prepare_video(video)


def run_long_batch(
    runtime: Runtime,
    clock: Clock,
    assets: Assets,
    *,
    error: WorkflowError | None = None,
    all_long: bool = False,
) -> tuple[str, RecordingGateway]:
    revision = configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(revision))
    assert batch
    gateway = RecordingGateway()
    live = offline_live_assets(runtime, assets)
    runner = batch_runner(runtime, clock, live, gateway)
    provider = LongContent(live, clock)
    provider.error, provider.all_long = error, all_long
    runner.providers.content = provider
    for _ in range(35):
        if not runner.once():
            break
    return batch.id, gateway


@pytest.mark.parametrize("all_long", [False, True])
def test_long_sources_complete_with_skips_without_upload_or_cleanup(
    runtime: Runtime, clock: Clock, assets: Assets, all_long: bool
) -> None:
    retained = runtime.artifacts.root / "production/synthetic-unsubmitted/original.mp4"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"not a successful upload")
    batch_id, gateway = run_long_batch(runtime, clock, assets, all_long=all_long)
    assert len(gateway.uploads) == (0 if all_long else 1)
    with runtime.factory() as db:
        batch = db.get(Batch, batch_id)
        assert batch and batch.status == "completed_with_skips"
        tasks = list(db.scalars(select(Task).where(Task.batch_id == batch_id)))
        skipped = verified_skips(db, batch_id, tasks)
        assert len(skipped) == (2 if all_long else 1)
        for task in tasks:
            if task.status != "skipped":
                continue
            setting = db.get(WorkflowSetting, PREFIX + task.id)
            assert setting and task.kind == "prepare_video" and task.attempts == 1
            record = SourceSkip.model_validate_json(setting.value_json)
            assert record.duration_seconds == 12760 and not record.publication_succeeded
            assert task.output_json is None
    assert retained.read_bytes() == b"not a successful upload"
    state = BatchPlanService(runtime.factory, runtime.policy, clock).state()
    assert not state.automatic_blocked and state.buffer_used == 0


@pytest.mark.parametrize(
    "code", ["SOURCE_DURATION_UNSUPPORTED", "SOURCE_LOGIN_REQUIRED", "SOURCE_DOWNLOAD_UNAVAILABLE"]
)
def test_unproven_duration_and_other_failures_remain_failures(
    runtime: Runtime, clock: Clock, assets: Assets, code: str
) -> None:
    batch_id, _ = run_long_batch(runtime, clock, assets, error=WorkflowError(code, "synthetic"))
    with runtime.factory() as db:
        tasks = list(db.scalars(select(Task).where(Task.batch_id == batch_id)))
        assert not verified_skips(db, batch_id, tasks)
        assert any(task.status == "failed" and task.error_code == code for task in tasks)


@pytest.mark.parametrize("duration", [0, 19, 1800, float("nan"), float("inf")])
def test_overlength_evidence_requires_a_finite_duration_above_limit(duration: float) -> None:
    with pytest.raises(ValueError):
        SourceTooLong("synthetic", duration)


def test_download_distinguishes_long_short_and_wrong_identity(tmp_path: Path) -> None:
    path = tmp_path / "not-downloaded.mp4"
    with pytest.raises(SourceTooLong):
        validate_download_identity("same", DownloadInfo(id="same", duration=1801), path)
    with pytest.raises(WorkflowError) as short:
        validate_download_identity("same", DownloadInfo(id="same", duration=19), path)
    assert short.value.code == "SOURCE_DURATION_UNSUPPORTED"
    with pytest.raises(WorkflowError) as wrong:
        validate_download_identity("same", DownloadInfo(id="other", duration=1801), path)
    assert wrong.value.code == "SOURCE_MEDIA_MISMATCH"
    path.write_bytes(b"synthetic")
    validate_download_identity("same", DownloadInfo(id="same", duration=1800), path)


@pytest.mark.parametrize("tamper", ["attempts", "cancelled"])
def test_tampered_source_skip_cannot_complete_or_release_capacity(
    runtime: Runtime, clock: Clock, assets: Assets, tamper: str
) -> None:
    batch_id, _ = run_long_batch(runtime, clock, assets)
    with runtime.factory.begin() as db:
        task = db.scalar(select(Task).where(Task.batch_id == batch_id, Task.status == "skipped"))
        assert task
        if tamper == "attempts":
            task.attempts += 1
        else:
            batch = db.get(Batch, batch_id)
            assert batch
            batch.status = "cancelled"
    with runtime.factory() as db:
        tasks = list(db.scalars(select(Task).where(Task.batch_id == batch_id)))
        with pytest.raises(WorkflowError):
            verified_skips(db, batch_id, tasks)
