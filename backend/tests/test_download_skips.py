from pathlib import Path

import pytest
from batch_pipeline_support import (
    FixtureSingleContent,
    RecordingGateway,
    batch_runner,
    offline_live_assets,
)
from conftest import Assets, Clock
from selection_support import discovery_items, initial_request
from sqlalchemy import select
from test_batch_trigger import configure, manual

from youmo_workflow.adapters.acquisition import download_error_code
from youmo_workflow.adapters.content_preparation import ContentProviders, ProductionContent
from youmo_workflow.domain.content import GenerationRequest, Transcript
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import VideoDTO
from youmo_workflow.domain.providers import SelectedContent
from youmo_workflow.domain.source_skip import (
    DOWNLOAD_SKIP_ERRORS,
    DownloadSkip,
    SourceDownloadFailed,
)
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.source_skips import PREFIX
from youmo_workflow.features.task_skips import verified_skips
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting


class FailingAcquisition:
    def __init__(self, code: str) -> None:
        self.code = code
        self.calls = 0

    def acquire(self, video: VideoDTO, directory: Path) -> Path:
        self.calls += 1
        (directory / "original.mp4.part").write_bytes(b"retained partial download")
        raise WorkflowError(self.code, "synthetic failure, no secret data")


class NoTranscription:
    def transcribe(self, video: VideoDTO, media: Path, directory: Path) -> Transcript:
        raise AssertionError("No ASR after failed download")


class NoModel:
    def generate(self, request: GenerationRequest) -> str:
        raise AssertionError("No paid model after failed download")


class DownloadContent(FixtureSingleContent):
    def __init__(self, assets: Assets, clock: Clock, preparation: ProductionContent) -> None:
        super().__init__(assets, clock)
        self.preparation = preparation

    def prepare_video(self, video: VideoDTO) -> SelectedContent:
        if not self.calls:
            self.calls.append(video.video_id)
            return self.preparation.prepare_video(video)
        return super().prepare_video(video)


@pytest.mark.parametrize("code", sorted(DOWNLOAD_SKIP_ERRORS))
def test_download_failure_skips_only_one_item_and_keeps_pipeline_moving(
    runtime: Runtime, clock: Clock, assets: Assets, code: str
) -> None:
    revision = configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(revision))
    assert batch
    gateway = RecordingGateway()
    live = offline_live_assets(runtime, assets)
    runner = batch_runner(runtime, clock, live, gateway)
    acquisition = FailingAcquisition(code)
    content = ProductionContent(
        runtime.artifacts, ContentProviders(acquisition, NoTranscription(), NoModel())
    )
    runner.providers.content = DownloadContent(live, clock, content)
    for _ in range(35):
        if not runner.once():
            break
    assert acquisition.calls == 1 and len(gateway.uploads) == 1
    with runtime.factory() as db:
        stored = db.get(Batch, batch.id)
        assert stored and stored.status == "completed_with_skips"
        tasks = list(db.scalars(select(Task).where(Task.batch_id == batch.id)))
        assert len(verified_skips(db, batch.id, tasks)) == 1
        skipped = next(task for task in tasks if task.status == "skipped")
        record = db.get(WorkflowSetting, PREFIX + skipped.id)
        assert record and skipped.kind == "prepare_video" and skipped.attempts == 1
        evidence = DownloadSkip.model_validate_json(record.value_json)
        assert evidence.error_code == code and not evidence.publication_succeeded
        assert skipped.output_json is None
    assert len(list(runtime.artifacts.root.rglob("source-download-failure.json"))) == 1
    assert len(list(runtime.artifacts.root.rglob("original.mp4.part"))) == 1
    state = BatchPlanService(runtime.factory, runtime.policy, clock).state()
    assert not state.automatic_blocked and state.buffer_used == 0


@pytest.mark.parametrize(
    "code",
    [
        "SOURCE_LOGIN_REQUIRED",
        "SOURCE_SESSION_AUTH_FAILED",
        "DISK_SPACE_LOW",
        "NODE_MISSING",
        "SOURCE_MEDIA_CHANGED",
    ],
)
def test_authentication_and_system_errors_are_not_silently_discarded(
    runtime: Runtime, code: str
) -> None:
    acquisition = FailingAcquisition(code)
    content = ProductionContent(
        runtime.artifacts, ContentProviders(acquisition, NoTranscription(), NoModel())
    )
    with pytest.raises(WorkflowError) as error:
        content.prepare_video(initial_request(1).items[0])
    assert error.value.code == code and not isinstance(error.value, SourceDownloadFailed)
    assert not list(runtime.artifacts.root.rglob("source-download-failure.json"))


class AcquiredMedia:
    def acquire(self, video: VideoDTO, directory: Path) -> Path:
        return directory / "original.mp4"


class FailedTranscription:
    def transcribe(self, video: VideoDTO, media: Path, directory: Path) -> Transcript:
        raise WorkflowError("SOURCE_DOWNLOAD_UNAVAILABLE", "not actually in the download phase")


def test_try_catch_does_not_swallow_downstream_failures(runtime: Runtime) -> None:
    content = ProductionContent(
        runtime.artifacts, ContentProviders(AcquiredMedia(), FailedTranscription(), NoModel())
    )
    with pytest.raises(WorkflowError) as error:
        content.prepare_video(initial_request(1).items[0])
    assert not isinstance(error.value, SourceDownloadFailed)
    assert not list(runtime.artifacts.root.rglob("source-download-failure.json"))


@pytest.mark.parametrize(
    "message",
    [
        "The uploader has not made this video available in your country",
        "This video is not available in your country",
    ],
)
def test_explicit_region_refusals_are_classified(message: str) -> None:
    assert download_error_code(message) == "SOURCE_REGION_RESTRICTED"


@pytest.mark.parametrize(
    "message",
    [
        "Network timeout in country lookup",
        "Video unavailable",
        "Sign in to confirm you're not a bot",
    ],
)
def test_unrelated_errors_are_not_labeled_region_refusals(message: str) -> None:
    assert download_error_code(message) != "SOURCE_REGION_RESTRICTED"
