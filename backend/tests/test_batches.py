from datetime import timedelta

import pytest
from conftest import Clock, sample_video

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.time import stamp
from youmo_workflow.runtime import Runtime


def test_daily_batch_idempotency(runtime: Runtime, clock: Clock) -> None:
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    first = runtime.batches.create(Policy())
    assert runtime.batches.create(Policy()).id == first.id
    assert first.candidate_count == 1


def test_snapshot_is_immutable(runtime: Runtime, clock: Clock) -> None:
    video = sample_video(clock)
    runtime.sources.ingest("tbsnewsdig", [video], stamp(clock()))
    batch = runtime.batches.create(Policy())
    video.title = "后续修改"
    runtime.sources.ingest(
        "tbsnewsdig", [video, sample_video(clock, "bcdefghijkl")], stamp(clock())
    )
    snapshot = runtime.batches.detail(batch.id)
    assert len(snapshot.items) == 1
    assert snapshot.items[0].title != "后续修改"


def test_time_window_excludes_old_and_future(runtime: Runtime, clock: Clock) -> None:
    records = [
        sample_video(clock, hours=73),
        sample_video(clock, "bcdefghijkl", -1),
        sample_video(clock, "cdefghijklm", 24),
    ]
    runtime.sources.ingest("tbsnewsdig", records, stamp(clock()))
    batch = runtime.batches.create(Policy())
    assert [v.video_id for v in runtime.batches.detail(batch.id).items] == ["cdefghijklm"]


def test_active_batch_blocks_next_day(runtime: Runtime, clock: Clock) -> None:
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    runtime.batches.create(Policy())
    clock.at += timedelta(days=1)
    with pytest.raises(WorkflowError, match="上一批"):
        runtime.batches.create(Policy())


def test_empty_batch_is_completed(runtime: Runtime) -> None:
    assert runtime.batches.create(Policy()).status == "completed"


def test_business_day_does_not_follow_utc_midnight(runtime: Runtime, clock: Clock) -> None:
    clock.at = clock.at.replace(hour=15, minute=59)
    assert runtime.batches.create(Policy()).local_date == "2026-11-06"
    clock.at += timedelta(minutes=2)
    assert runtime.batches.create(Policy()).local_date == "2026-11-07"
