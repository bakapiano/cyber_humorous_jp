import pytest
from conftest import Clock, sample_video

from youmo_workflow.adapters.youtube_duration import duration_seconds
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.time import stamp
from youmo_workflow.runtime import Runtime


@pytest.mark.parametrize(
    "value,expected",
    [("PT18M26S", 1106), ("PT1H2M3S", 3723), ("P1DT1S", 86401), ("PT0S", 0), (None, None)],
)
def test_duration_parsing(value: str | None, expected: int | None) -> None:
    assert duration_seconds(value) == expected


@pytest.mark.parametrize("value", ["P", "PT", "PT-1S", "unknown"])
def test_invalid_duration_is_not_invented(value: str) -> None:
    with pytest.raises(ValueError):
        duration_seconds(value)


def test_duration_enrichment_preserves_frozen_snapshots(runtime: Runtime, clock: Clock) -> None:
    video = sample_video(clock)
    runtime.sources.ingest("tbsnewsdig", [video], stamp(clock()))
    batch = runtime.batches.create(Policy())
    video.duration_seconds = 1106
    runtime.sources.ingest("tbsnewsdig", [video], stamp(clock()))
    current = runtime.sources.list_videos("", 0, 10).items[0]
    assert current.duration_seconds == 1106 and current.revision == 2
    assert runtime.batches.detail(batch.id).items[0].duration_seconds is None
    video.duration_seconds = None
    runtime.sources.ingest("tbsnewsdig", [video], stamp(clock()))
    assert runtime.sources.list_videos("", 0, 10).items[0].duration_seconds == 1106
