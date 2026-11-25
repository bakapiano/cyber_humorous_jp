import json
from pathlib import Path

from conftest import Clock, sample_video
from fastapi.testclient import TestClient
from usage_support import write_call

from youmo_workflow.adapters.usage_journal import RawUsage, UsageJournal
from youmo_workflow.api import create_app
from youmo_workflow.domain.time import stamp
from youmo_workflow.domain.usage import UsageReport
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task


def test_physical_retries_and_corrections_count_but_duplicate_responses_do_not(
    tmp_path: Path,
) -> None:
    stage = tmp_path / "production/content/translation"
    write_call(stage, "one")
    write_call(stage / "network/00", "one")
    write_call(stage / "network/01", "two")
    write_call(stage / "corrections/01/network/00", "three")
    write_call(stage / "network/02", "missing", missing_usage=True)
    events, duplicates = UsageJournal(tmp_path).snapshot()
    assert duplicates == 1 and len(events) == 4
    assert sum(event.retry for event in events) == 3
    assert sum(event.usage is not None for event in events) == 3
    assert all(event.content_id == "content" for event in events)


def test_selection_raw_and_corrections_bind_to_the_frozen_batch(tmp_path: Path) -> None:
    root = tmp_path / "selection"
    journal = root / "v008/rescue/batch"
    journal.mkdir(parents=True)
    (journal / "request.json").write_text(
        json.dumps(
            {
                "request": {"batch_id": "batch"},
                "request_sha256": "a" * 64,
                "prompt_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    write_call(root / "raw" / ("a" * 64) / ("b" * 64) / "network/00", "original")
    write_call(journal / "corrections/01/network/00", "repair")
    events, duplicates = UsageJournal(tmp_path).snapshot()
    assert duplicates == 0 and len(events) == 2
    assert all(event.batch_id == "batch" and event.stage == "初筛补漏" for event in events)


def test_cache_detail_fallback_and_missing_split_are_not_invented() -> None:
    detail = RawUsage.model_validate(
        {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 80},
        }
    ).tokens()
    assert detail.cache_hit == 80 and detail.cache_miss == 20
    unknown = RawUsage(prompt_tokens=100, completion_tokens=20).tokens()
    assert unknown.cache_hit is None and unknown.cache_miss is None


def test_missing_response_is_visible_and_new_response_refreshes_cache(tmp_path: Path) -> None:
    directory = tmp_path / "production/content/stage/network/00"
    directory.mkdir(parents=True)
    (directory / "attempt.json").write_text("{}", encoding="utf-8")
    (directory / "payload.json").write_text("{}", encoding="utf-8")
    reader = UsageJournal(tmp_path)
    events, _ = reader.snapshot()
    assert len(events) == 1 and events[0].usage is None
    write_call(directory, "arrived")
    events, _ = reader.snapshot()
    assert len(events) == 1 and events[0].usage and events[0].usage.input == 100


def test_api_is_read_only_private_and_attributed(runtime: Runtime, clock: Clock) -> None:
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    at = stamp(clock())
    with runtime.factory.begin() as db:
        db.add(
            Batch(
                id="batch",
                local_date="2026-11-06",
                status="running",
                snapshot_at=at,
                policy_json=runtime.policy.model_dump_json(),
                candidate_count=50,
            )
        )
        db.add(
            Task(
                id="task",
                unique_key="task",
                kind="prepare_video",
                batch_id="batch",
                status="succeeded",
                input_json="{}",
                output_json=json.dumps({"content_id": "content", "video_id": "abcdefghijk"}),
                created_at=at,
                updated_at=at,
                next_run_at=at,
            )
        )
    path = write_call(
        runtime.settings.root / "data/production/content/translation/network/00", "real-fixture"
    )
    before = path.read_bytes()
    with TestClient(create_app(runtime)) as client:
        response = client.get("/api/v1/model-usage")
        assert response.status_code == 200
        report = UsageReport.model_validate_json(response.content)
        assert report.summary.calls == 1 and report.summary.total_tokens == 300
        assert report.videos[0].id == "abcdefghijk" and report.workflows[0].id == "batch"
        assert report.unattributed_workflow.calls == 0
        assert (
            "NEVER_RETURN_THIS" not in response.text and "PRIVATE_MODEL_TEXT" not in response.text
        )
        assert str(runtime.settings.root) not in response.text
        assert client.get("/api/v1/model-usage?refresh=true").status_code == 200
    assert path.read_bytes() == before


def test_api_filters_incomplete_calls_without_modifying_the_journals(runtime: Runtime) -> None:
    root = runtime.settings.root / "data/production"
    good = write_call(root / "complete/translation/network/00", "complete")
    absent = write_call(root / "missing/translation/network/00", "missing", missing_usage=True)
    legacy = write_call(root / "legacy/translation/network/00", "legacy")
    body = json.loads(legacy.read_bytes())
    del body["created"]
    del body["usage"]["prompt_cache_hit_tokens"]
    del body["usage"]["prompt_cache_miss_tokens"]
    legacy.write_text(json.dumps(body), encoding="utf-8")
    before = {path: path.read_bytes() for path in (good, absent, legacy)}
    with TestClient(create_app(runtime)) as client:
        report = UsageReport.model_validate_json(client.get("/api/v1/model-usage").content)
    assert report.summary.calls == report.summary.metered_calls == 1
    assert report.summary.total_tokens == 300
    assert report.summary.unmetered_calls == report.summary.unpriced_calls == 0
    assert [video.id for video in report.videos] == ["complete"]
    assert report.warnings == []
    assert all(path.read_bytes() == original for path, original in before.items())
