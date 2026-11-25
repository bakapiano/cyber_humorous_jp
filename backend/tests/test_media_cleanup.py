from pathlib import Path

import pytest
from batch_pipeline_support import RecordingGateway, batch_runner, offline_live_assets
from conftest import Assets, Clock
from selection_support import discovery_items
from sqlalchemy import select
from test_batch_trigger import configure, manual

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.orchestration.cleanup import BatchMediaCleanup, checked_path
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task


def ready_cleanup(runtime: Runtime, clock: Clock, assets: Assets) -> Task:
    revision = configure(runtime, clock, count=1)
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(revision))
    runner = batch_runner(runtime, clock, offline_live_assets(runtime, assets), RecordingGateway())
    for _ in range(20):
        with runtime.factory() as db:
            found = db.scalar(select(Task).where(Task.kind == "cleanup_batch"))
        if found:
            task = runtime.tasks.claim(("cleanup_batch",))
            assert task
            return task
        assert runner.once()
    raise AssertionError("Expected a cleanup task")


def test_cleanup_removes_only_batch_videos_keeps_llm_and_nonvideo_assets(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    task = ready_cleanup(runtime, clock, assets)
    cleanup = BatchMediaCleanup(runtime)
    content = cleanup.verified_contents(task)[0]
    directory = runtime.artifacts.root / "production" / content
    directory.mkdir(parents=True)
    video = directory / "original.mp4"
    video.write_bytes(b"synthetic-video")
    ref = runtime.artifacts.register(video, "media")
    kept = [
        directory / "request.json",
        directory / "response.json",
        directory / "final.zh-ja.srt",
        directory / "cover.png",
    ]
    for path in kept:
        path.write_text("keep exactly", encoding="utf-8")
    ledger = runtime.settings.root / "data/production" / content / "editing_v003/response.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("raw LLM output", encoding="utf-8")
    outside = runtime.artifacts.root / "outside.mp4"
    outside.write_bytes(b"preserved")
    result = cleanup.run(task)
    assert not video.exists() and outside.exists()
    assert all(path.read_text(encoding="utf-8") == "keep exactly" for path in kept)
    assert ledger.read_text(encoding="utf-8") == "raw LLM output"
    assert '"video_files":1' in result
    assert not runtime.artifacts.describe(ref.artifact_id).available
    with pytest.raises(WorkflowError, match="清理"):
        runtime.artifacts.resolve(ref, "media")
    with runtime.factory() as db:
        resumed = db.get(Task, task.id)
        assert resumed
        assert cleanup.run(resumed) == result


def test_unknown_publication_prevents_cleanup(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    task = ready_cleanup(runtime, clock, assets)
    publication = runtime.publications.list_publications()[0]
    with runtime.factory.begin() as db:
        runtime.publications.require(db, publication.id).status = "unknown"
    with pytest.raises(WorkflowError, match="未确认投递"):
        BatchMediaCleanup(runtime).run(task)


def test_cleanup_rejects_outside_roots_and_root_directory(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    for path in (root, root / "production", tmp_path / "outside.mp4"):
        with pytest.raises(WorkflowError, match="越界"):
            checked_path(root, path)
