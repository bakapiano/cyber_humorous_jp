from pathlib import Path

import httpx
import pytest
from selection_support import completion, frozen_call, initial_request

from youmo_workflow.adapters.initial_selection import V004InitialSelector, validate_ranking
from youmo_workflow.adapters.model_http import HttpRetryPolicy
from youmo_workflow.adapters.selection_completion import DeepSeekCompletion
from youmo_workflow.adapters.selection_journal import SelectionJournal
from youmo_workflow.adapters.selection_models import FrozenInitialCall
from youmo_workflow.adapters.selection_profile import V004Profile
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.selection import InitialRequest, ModelRanking


class FixtureProfile(V004Profile):
    def freeze(self, request: InitialRequest) -> FrozenInitialCall:
        return frozen_call(request)


def test_v004_ranks_fifty_and_keeps_thirty_without_content_claims() -> None:
    call = frozen_call(initial_request())
    result = validate_ranking(call, completion(call))
    assert len(result.ranked) == 50 and len(result.shortlisted_ids) == 30
    assert len(result.not_shortlisted_ids) == 25
    assert result.content_verified is False
    assert result.shortlisted_ids == [row.video.video_id for row in result.ranked[:30]]


def test_small_pool_is_not_padded() -> None:
    call = frozen_call(initial_request(2))
    result = validate_ranking(call, completion(call))
    assert result.rank_count == 2 and len(result.shortlisted_ids) == 2


def test_unknown_duplicate_truncated_and_fabricated_evidence_are_errors() -> None:
    call = frozen_call(initial_request(2))
    original = completion(call)
    assert original.content is not None
    ranking = ModelRanking.model_validate_json(original.content)
    for changed in ["unknown", ranking.ranked_candidates[1].candidate_id]:
        altered = ranking.model_copy(deep=True)
        altered.ranked_candidates[0].candidate_id = changed
        with pytest.raises(WorkflowError, match="排序"):
            validate_ranking(
                call, original.model_copy(update={"content": altered.model_dump_json()})
            )
    altered = ranking.model_copy(deep=True)
    altered.ranked_candidates[0].evidence = "根本不存在的标题原文"
    with pytest.raises(WorkflowError, match="连续原文"):
        validate_ranking(call, original.model_copy(update={"content": altered.model_dump_json()}))
    with pytest.raises(WorkflowError, match="截断"):
        validate_ranking(call, original.model_copy(update={"finish_reason": "length"}))


def test_saved_response_is_reused_and_reasoning_or_key_never_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic-only")
    request = initial_request(2)
    calls: list[httpx.Request] = []

    def transport(sent: httpx.Request) -> httpx.Response:
        calls.append(sent)
        assert b"sk-synthetic-only" not in sent.content
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": completion(frozen_call(request)).content,
                            "reasoning_content": "do-not-store-private-reasoning",
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        provider = V004InitialSelector(
            FixtureProfile(tmp_path),
            SelectionJournal(tmp_path / "runs"),
            DeepSeekCompletion(client, tmp_path / "unused"),
        )
        first = provider.rank(request)
        second = provider.rank(request)
    assert first == second and len(calls) == 1
    saved = (tmp_path / "runs/synthetic-batch/response.json").read_text(encoding="utf-8")
    assert "sk-synthetic-only" not in saved and "do-not-store-private-reasoning" not in saved


def test_timeout_or_manual_retry_cannot_repeat_a_paid_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic-only")
    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("sensitive diagnostics must not leak", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        provider = V004InitialSelector(
            FixtureProfile(tmp_path),
            SelectionJournal(tmp_path / "runs"),
            DeepSeekCompletion(client, tmp_path / "unused"),
        )
        for _ in range(2):
            with pytest.raises(WorkflowError) as error:
                provider.rank(initial_request(2))
            assert error.value.code == "MODEL_ATTEMPT_UNRESOLVED"
            assert "sensitive" not in error.value.message
    assert calls == 1


def test_journal_rejects_input_changes_and_path_escape(tmp_path: Path) -> None:
    journal = SelectionJournal(tmp_path)
    request = initial_request(2)
    journal.freeze(request, frozen_call)
    request.items[0].title = "changed title"
    with pytest.raises(WorkflowError, match="不同的冻结输入"):
        journal.freeze(request, frozen_call)
    with pytest.raises(WorkflowError, match="批次标识"):
        journal.folder("../escape")


def test_missing_key_does_not_consume_attempt_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500))
    ) as client:
        provider = V004InitialSelector(
            FixtureProfile(tmp_path),
            SelectionJournal(tmp_path / "runs"),
            DeepSeekCompletion(client, tmp_path / "absent"),
        )
        with pytest.raises(WorkflowError) as error:
            provider.rank(initial_request(2))
    assert error.value.code == "SELECTION_KEY_MISSING"
    assert not (tmp_path / "runs/synthetic-batch/attempt.json").exists()


def test_initial_json_failure_is_corrected_with_original_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic-only")
    request = initial_request(2)
    sent: list[str] = []

    def transport(value: httpx.Request) -> httpx.Response:
        sent.append(value.content.decode())
        content = "{}" if len(sent) == 1 else completion(frozen_call(request)).content
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        selector = V004InitialSelector(
            FixtureProfile(tmp_path),
            SelectionJournal(tmp_path / "runs"),
            DeepSeekCompletion(client, tmp_path / "unused"),
        )
        assert len(selector.rank(request).ranked) == 2
        assert len(selector.rank(request).ranked) == 2
    assert len(sent) == 2 and "ranked_candidates" in sent[1]
    assert (tmp_path / "runs/synthetic-batch/corrections/01/attempt.json").exists()


def test_selection_http_retry_uses_the_same_frozen_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    request = initial_request(2)
    sent: list[bytes] = []

    def handle(value: httpx.Request) -> httpx.Response:
        sent.append(value.content)
        if len(sent) == 1:
            return httpx.Response(503, text="temporary")
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": completion(frozen_call(request)).content},
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = DeepSeekCompletion(
            client,
            tmp_path / "unused",
            tmp_path / "raw",
            HttpRetryPolicy(enabled=True, delay_seconds=0),
        )
        selector = V004InitialSelector(
            FixtureProfile(tmp_path), SelectionJournal(tmp_path / "runs"), model
        )
        assert len(selector.rank(request).ranked) == 2
        assert len(selector.rank(request).ranked) == 2
    assert len(sent) == 2 and sent[0] == sent[1]
    assert len(list((tmp_path / "raw").rglob("network/01/attempt.json"))) == 1
