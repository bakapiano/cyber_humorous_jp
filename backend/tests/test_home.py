from pathlib import Path

import httpx
import pytest
from conftest import Clock, sample_video

from youmo_workflow.adapters.home import NodeHomeProvider
from youmo_workflow.adapters.youtube import GoogleChannelProvider
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.features.discovery import enqueue_home
from youmo_workflow.features.sources import known_channel_ids
from youmo_workflow.runtime import Runtime


def test_reference_channel_identity_map_is_complete() -> None:
    path = Path(__file__).resolve().parents[2] / "config/sources.json"
    assert len(known_channel_ids(path)) == 102


def test_home_discovery_is_daily_idempotent(runtime: Runtime, clock: Clock) -> None:
    assert enqueue_home(runtime.factory, clock).id == enqueue_home(runtime.factory, clock).id


def test_recommendations_do_not_advance_channel_cursor(runtime: Runtime, clock: Clock) -> None:
    video = sample_video(clock)
    video.origin = "home"
    result = runtime.sources.ingest_recommendations([video], {"UC" + "a" * 22: "tbsnewsdig"})
    assert result["accepted"] == 1
    assert runtime.sources.list_sources()[0].last_checked is None
    assert runtime.sources.list_videos("", 0, 10).items[0].origin == "home"


def test_unknown_channels_are_not_claimed_as_reference_sources(
    runtime: Runtime, clock: Clock
) -> None:
    result = runtime.sources.ingest_recommendations([sample_video(clock)], {})
    assert result["outside_reference_sources"] == 1
    assert runtime.sources.list_videos("", 0, 10).total == 0


def test_missing_profile_does_not_fall_back(tmp_path: Path) -> None:
    token = tmp_path / "token.txt"
    token.write_text("isolated-test-token", encoding="utf-8")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                409,
                json={
                    "code": "PROFILE_NOT_CONFIGURED",
                    "message": "private value must not escape",
                },
            )
        )
    ) as client:
        with pytest.raises(WorkflowError) as error:
            NodeHomeProvider(token, client).discover()
    assert error.value.code == "PROFILE_NOT_CONFIGURED"
    assert "private value" not in str(error.value)


def test_home_pages_deduplicate_video_ids(tmp_path: Path) -> None:
    token = tmp_path / "token.txt"
    token.write_text("isolated-test-token", encoding="utf-8")
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "items": [{"video_id": "abcdefghijk", "title": "测试推荐", "origin": "home"}],
                "continuation": "next" if calls == 1 else None,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = NodeHomeProvider(token, client).discover()
    assert calls == 2 and len(result.items) == 1


def test_recommendation_dates_come_from_data_api() -> None:
    payload = {
        "items": [
            {
                "id": "abcdefghijk",
                "snippet": {
                    "title": "元数据补全测试",
                    "channelId": "UC" + "a" * 22,
                    "channelTitle": "TBS",
                    "publishedAt": "2026-11-06T01:00:00Z",
                },
            }
        ]
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        items = GoogleChannelProvider("isolated-test-key", client).lookup(["abcdefghijk"])
    assert items[0].published_at == "2026-11-06T01:00:00+00:00"
    assert items[0].origin == "home"
