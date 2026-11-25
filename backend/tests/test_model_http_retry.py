import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import httpx
import pytest
from pydantic import BaseModel

from youmo_workflow.adapters.model_http import HttpRetryPolicy
from youmo_workflow.adapters.production_model import ProductionModel
from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.errors import WorkflowError


class Result(BaseModel):
    value: int


def ok(content: str = '{"value":1}') -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        },
    )


def policy() -> HttpRetryPolicy:
    return HttpRetryPolicy(
        enabled=True, delay_seconds=0, authorization="synthetic user authorization"
    )


@pytest.mark.parametrize("failure", ["timeout", "http"])
def test_authorized_transient_retry_is_durable_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    sent: list[bytes] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        if len(sent) < 3:
            if failure == "timeout":
                raise httpx.ReadTimeout("sensitive diagnostics")
            return httpx.Response(503, text="synthetic error body")
        return ok()

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        request = GenerationRequest(content_id="test", stage="editing_v003", prompt="source")
        for _ in range(2):
            assert (
                ProductionModel(client, tmp_path / "unused", tmp_path, policy()).generate_checked(
                    request, Result.model_validate_json
                )
                == '{"value":1}'
            )
    assert len(sent) == 3 and len(set(sent)) == 1
    directory = tmp_path / "production/test/editing_v003"
    assert len(list((directory / "network").glob("*/attempt.json"))) == 3
    assert json.loads((directory / "transport-result.json").read_text())["accepted_attempt"] == 2
    assert "sensitive diagnostics" not in "".join(
        path.read_text(encoding="utf-8") for path in directory.rglob("*.json")
    )


def test_exhausted_network_budget_cannot_reset_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(429, text="rate limited")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        request = GenerationRequest(content_id="test", stage="title_v004", prompt="source")
        for _ in range(2):
            with pytest.raises(WorkflowError) as error:
                ProductionModel(client, tmp_path / "unused", tmp_path, policy()).generate(request)
            assert error.value.code == "MODEL_HTTP_RETRIES_EXHAUSTED"
            assert "HTTP 429" in error.value.message
    assert len(calls) == 3


@pytest.mark.parametrize("status", [400, 401, 402, 403])
def test_permanent_rejection_is_not_repeated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(status, text="synthetic permanent refusal")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        request = GenerationRequest(content_id="test", stage="title_v004", prompt="source")
        for _ in range(2):
            with pytest.raises(WorkflowError):
                ProductionModel(client, tmp_path / "unused", tmp_path, policy()).generate(request)
    assert len(calls) == 1


def test_new_authorization_resumes_legacy_http_failure_without_overwriting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, text="old response") if len(calls) == 1 else ok()

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        request = GenerationRequest(content_id="test", stage="editing_v003", prompt="source")
        with pytest.raises(WorkflowError):
            ProductionModel(client, tmp_path / "unused", tmp_path).generate(request)
        root = tmp_path / "production/test/editing_v003"
        before = (root / "provider-response.json").read_bytes()
        assert (
            ProductionModel(client, tmp_path / "unused", tmp_path, policy()).generate_checked(
                request, Result.model_validate_json
            )
            == '{"value":1}'
        )
        assert (root / "provider-response.json").read_bytes() == before
    assert len(calls) == 2 and (root / "network/01/response.json").exists()
    assert not (root / "network/00/attempt.json").exists()


def test_format_correction_can_recover_its_own_network_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    sent: list[bytes] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        if len(sent) == 1:
            return ok("not JSON")
        return httpx.Response(503, text="temporary") if len(sent) == 2 else ok()

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        request = GenerationRequest(content_id="test", stage="title_v004", prompt="source")
        for _ in range(2):
            assert (
                ProductionModel(client, tmp_path / "unused", tmp_path, policy()).generate_checked(
                    request, Result.model_validate_json
                )
                == '{"value":1}'
            )
    assert len(sent) == 3 and sent[1] == sent[2]
    assert (
        tmp_path / "production/test/title_v004/corrections/01/network/01/response.json"
    ).exists()


def test_live_request_lock_prevents_concurrent_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    entered, release = Event(), Event()
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        entered.set()
        assert release.wait(5)
        return ok()

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        model = ProductionModel(client, tmp_path / "unused", tmp_path, policy())
        request = GenerationRequest(content_id="test", stage="title_v004", prompt="source")
        running = pool.submit(model.generate, request)
        try:
            assert entered.wait(5)
            with pytest.raises(WorkflowError) as error:
                model.generate(request)
            assert error.value.code == "MODEL_REQUEST_IN_FLIGHT"
        finally:
            release.set()
        assert running.result() == '{"value":1}'
    assert len(calls) == 1
