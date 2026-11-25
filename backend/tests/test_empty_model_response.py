from pathlib import Path

import httpx
import pytest
from test_model_http_retry import Result, ok, policy

from youmo_workflow.adapters.production_model import ProductionModel
from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.errors import WorkflowError


def legacy_empty_detection(_directory: Path) -> bool:
    return False


@pytest.mark.parametrize("body", [b"null", b"\nnull", b" \n"])
def test_empty_transport_response_uses_only_remaining_network_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls: list[bytes] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        return httpx.Response(200, content=body) if len(calls) < 3 else ok()

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path, policy())
        request = GenerationRequest(content_id="empty", stage="title_v004", prompt="source")
        for _ in range(2):
            assert model.generate_checked(request, Result.model_validate_json) == '{"value":1}'
    assert len(calls) == 3 and len(set(calls)) == 1
    assert len(list(tmp_path.rglob("empty-protocol-recovery.json"))) == 2


def test_historical_null_terminal_is_preserved_and_budget_not_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(500, text="temporary upstream failure")
        return httpx.Response(200, content=b"null") if len(calls) == 2 else ok()

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path, policy())
        request = GenerationRequest(content_id="legacy", stage="title_v004", prompt="source")
        with monkeypatch.context() as legacy:
            legacy.setattr(
                "youmo_workflow.adapters.model_http.empty_protocol_response", legacy_empty_detection
            )
            with pytest.raises(WorkflowError) as error:
                model.generate(request)
            assert error.value.code == "MODEL_RESPONSE_INVALID"
        old = tmp_path / "production/legacy/title_v004/network/01/terminal-error.json"
        before = old.read_bytes()
        assert model.generate_checked(request, Result.model_validate_json) == '{"value":1}'
        assert old.read_bytes() == before
    assert len(calls) == 3


def test_repeated_null_exhausts_without_restart_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, content=b"null")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path, policy())
        request = GenerationRequest(content_id="bounded", stage="title_v004", prompt="source")
        for _ in range(2):
            with pytest.raises(WorkflowError) as error:
                model.generate(request)
            assert error.value.code == "MODEL_HTTP_RETRIES_EXHAUSTED"
    assert len(calls) == 3
