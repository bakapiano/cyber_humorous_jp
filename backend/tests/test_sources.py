import json

import httpx
import pytest
from conftest import Clock, sample_video

from youmo_workflow.adapters.youtube import GoogleChannelProvider
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.runtime import Runtime


def test_ingestion_is_idempotent(runtime: Runtime, clock: Clock) -> None:
    video = sample_video(clock)
    assert runtime.sources.ingest("tbsnewsdig", [video], stamp(clock())) == 1
    assert runtime.sources.ingest("tbsnewsdig", [video], stamp(clock())) == 0
    assert runtime.sources.list_videos("", 0, 10).total == 1


def test_changed_metadata_increments_revision(runtime: Runtime, clock: Clock) -> None:
    video = sample_video(clock)
    runtime.sources.ingest("tbsnewsdig", [video], stamp(clock()))
    video.title = "修订后的测试标题"
    runtime.sources.ingest("tbsnewsdig", [video], stamp(clock()))
    assert runtime.sources.list_videos("", 0, 10).items[0].revision == 2


def test_error_does_not_advance_cursor(runtime: Runtime, clock: Clock) -> None:
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    previous = runtime.sources.list_sources()[0].last_checked
    runtime.sources.record_failure("tbsnewsdig", "connection failed")
    assert runtime.sources.list_sources()[0].last_checked == previous


def channel_response() -> dict[str, object]:
    return {
        "items": [
            {
                "id": "UC" + "a" * 22,
                "snippet": {"title": "TBS"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UUtest"}},
            }
        ]
    }


def test_google_fetch_uses_key_only_for_metadata() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("channels"):
            return httpx.Response(200, json=channel_response())
        if request.url.path.endswith("videos"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "abcdefghijk",
                            "snippet": {
                                "title": "真实结构测试",
                                "channelTitle": "TBS",
                                "channelId": "UC" + "a" * 22,
                                "publishedAt": "2026-11-06T01:00:00Z",
                            },
                            "contentDetails": {"duration": "PT18M26S"},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "snippet": {"title": "真实结构测试", "channelTitle": "TBS"},
                        "contentDetails": {
                            "videoId": "abcdefghijk",
                            "videoPublishedAt": "2026-11-06T01:00:00Z",
                        },
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = GoogleChannelProvider("sentinel-key", client).collect(
            "tbsnewsdig", "2026-11-03T00:00:00Z"
        )
    assert result[0].video_id == "abcdefghijk"
    assert result[0].duration_seconds == 1106 and result[0].origin == "channel"
    assert all(
        r.url.host == "www.googleapis.com" and r.url.params["key"] == "sentinel-key" for r in seen
    )
    assert "sentinel-key" not in result[0].model_dump_json()


def test_upstream_error_never_exposes_key() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403))) as client:
        with pytest.raises(WorkflowError) as error:
            GoogleChannelProvider("private-key", client).collect(
                "tbsnewsdig", "2026-11-03T00:00:00Z"
            )
    assert "private-key" not in str(error.value)
    assert not error.value.retryable


def test_catalog_is_real_and_defaults_are_bounded(runtime: Runtime) -> None:
    from pathlib import Path

    from youmo_workflow.features.sources import seed_sources

    path = Path(__file__).resolve().parents[2] / "config/sources.json"
    count = seed_sources(runtime.factory, path)
    assert count == len(json.loads(path.read_text(encoding="utf-8")))
    rows = runtime.sources.list_sources()
    assert len(rows) == 102
    assert {row.id for row in rows if row.enabled} == {"tbsnewsdig", "ntv_news", "ANNnewsCH"}
