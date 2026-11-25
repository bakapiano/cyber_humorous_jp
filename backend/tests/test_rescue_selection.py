from pathlib import Path

import httpx
import pytest
from rescue_support import FixtureRescueProfile, http_result, provider, rescue_completion
from selection_support import completion, initial_request

from youmo_workflow.adapters.rescue_composition import compose_output, rescue_call
from youmo_workflow.adapters.rescue_profile import V008Profile
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.selection import ModelRanking


def test_two_calls_are_cached_separately_and_preserve_the_first_twenty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic-only")
    request = initial_request(60)
    call = FixtureRescueProfile(tmp_path).freeze(request)
    sent: list[str] = []

    def transport(value: httpx.Request) -> httpx.Response:
        sent.append(value.content.decode())
        return http_result(rescue_completion(call) if "RESCUE" in sent[-1] else completion(call))

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        selector = provider(tmp_path, client)
        first = selector.rank(request)
        assert selector.rank(request) == first
    assert len(sent) == 2 and all("sk-synthetic-only" not in body for body in sent)
    assert first.version == "v008" and first.composition == "anchor_rescue"
    assert [row.video.video_id for row in first.ranked[:20]] == [
        row.video_id for row in request.items[:20]
    ]
    assert [row.video.video_id for row in first.ranked[20:]] == [
        row.video_id for row in request.items[30:60]
    ]
    assert len(first.shortlisted_ids) == 30
    for stage in ("baseline", "rescue"):
        raw = (tmp_path / f"v008/{stage}/synthetic-batch/response.json").read_text(encoding="utf-8")
        assert "not-retained" not in raw and "sk-synthetic-only" not in raw
    assert (tmp_path / "v008/rescue/synthetic-batch/ranking.json").is_file()


def test_rescue_content_filter_with_null_content_is_an_explicit_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic-only")
    request = initial_request(60)
    call = FixtureRescueProfile(tmp_path).freeze(request)

    def transport(sent: httpx.Request) -> httpx.Response:
        result = completion(call)
        if b"RESCUE" in sent.content:
            result = result.model_copy(update={"finish_reason": "content_filter", "content": None})
        return http_result(result)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        output = provider(tmp_path, client).rank(request)
    assert output.composition == "baseline_fallback"
    assert output.fallback_reason == "provider_content_filter"
    assert output.shortlisted_ids == [row.video_id for row in request.items[:30]]
    assert '"content":null' in (tmp_path / "v008/rescue/synthetic-batch/response.json").read_text()


def test_baseline_failure_makes_no_rescue_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic-only")
    request = initial_request(60)
    call = FixtureRescueProfile(tmp_path).freeze(request)
    requests: list[httpx.Request] = []

    def transport(sent: httpx.Request) -> httpx.Response:
        requests.append(sent)
        return http_result(
            completion(call).model_copy(update={"finish_reason": "content_filter", "content": None})
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        selector = provider(tmp_path, client)
        for _ in range(2):
            with pytest.raises(WorkflowError):
                selector.rank(request)
    assert len(requests) == 1
    assert not (tmp_path / "v008/rescue/synthetic-batch/attempt.json").exists()


def test_rescue_timeout_does_not_repeat_either_component_or_silently_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic-only")
    request = initial_request(60)
    call = FixtureRescueProfile(tmp_path).freeze(request)
    count = 0

    def transport(sent: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if b"RESCUE" in sent.content:
            raise httpx.ReadTimeout("synthetic", request=sent)
        return http_result(completion(call))

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        selector = provider(tmp_path, client)
        for _ in range(2):
            with pytest.raises(WorkflowError) as error:
                selector.rank(request)
            assert error.value.code == "MODEL_ATTEMPT_UNRESOLVED"
    assert count == 2
    assert not (tmp_path / "v008/rescue/synthetic-batch/ranking.json").exists()


def test_anchor_overlap_and_non_filter_failure_are_not_valid_fallbacks(tmp_path: Path) -> None:
    call = FixtureRescueProfile(tmp_path).freeze(initial_request(60))
    baseline, rescue = completion(call), rescue_completion(call)
    assert rescue.content is not None
    rows = ModelRanking.model_validate_json(rescue.content)
    rows.ranked_candidates[0].candidate_id = "C001"
    with pytest.raises(WorkflowError):
        compose_output(
            call, baseline, rescue.model_copy(update={"content": rows.model_dump_json()}), "hash"
        )
    with pytest.raises(WorkflowError):
        compose_output(
            call, baseline, rescue.model_copy(update={"finish_reason": "length"}), "hash"
        )
    frozen_rescue = rescue_call(call, baseline)
    assert len(frozen_rescue.candidates) == 40
    assert "C001" not in {row.candidate_id for row in frozen_rescue.candidates}


def test_small_pool_is_rejected_before_profile_or_model_access(tmp_path: Path) -> None:
    with pytest.raises(WorkflowError) as error:
        V008Profile(tmp_path).freeze(initial_request(49))
    assert error.value.code == "SELECTION_POOL_SIZE"


def test_rescue_format_repair_does_not_repeat_valid_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic-only")
    request = initial_request(60)
    call = FixtureRescueProfile(tmp_path).freeze(request)
    sent: list[str] = []

    def transport(value: httpx.Request) -> httpx.Response:
        sent.append(value.content.decode())
        if "RESCUE" not in sent[-1]:
            return http_result(completion(call))
        result = rescue_completion(call)
        return http_result(
            result.model_copy(update={"content": "{}"}) if len(sent) == 2 else result
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        selector = provider(tmp_path, client)
        result = selector.rank(request)
        assert selector.rank(request) == result
    assert len(sent) == 3 and sum("RESCUE" not in body for body in sent) == 1
    assert result.composition == "anchor_rescue"
    assert (tmp_path / "v008/rescue/synthetic-batch/corrections/01/attempt.json").exists()
