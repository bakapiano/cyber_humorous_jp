import json
from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from youmo_workflow.adapters.model_http import HttpRetryPolicy, RetryingCompletion
from youmo_workflow.adapters.model_records import recover_completion
from youmo_workflow.adapters.model_route import WORKBUDDY_MODEL, ModelRoute, read_workbuddy_key
from youmo_workflow.domain.errors import WorkflowError

PAYLOAD: dict[str, JsonValue] = {
    "model": "deepseek-flash",
    "max_tokens": 131072,
    "reasoning_effort": "high",
    "messages": [{"role": "user", "content": "synthetic"}],
}
KEY = "synthetic-local-gateway-key"


def test_workbuddy_preserves_logical_identity_and_raw_receipt(tmp_path: Path) -> None:
    def reply(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:7863/v1/chat/completions"
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        payload = json.loads(request.content)
        assert payload["model"] == WORKBUDDY_MODEL
        assert payload["max_tokens"] == 128000
        assert payload["reasoning_effort"] == "high"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4.1-flash",
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {"credit": 0.02},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        transport = RetryingCompletion(client, HttpRetryPolicy(enabled=True), ModelRoute.WORKBUDDY)
        result = transport.send(PAYLOAD, KEY, "synthetic", tmp_path)
    assert result.model == "deepseek-flash"
    attempt = tmp_path / "network/00"
    assert json.loads((attempt / "payload.json").read_bytes()) == PAYLOAD
    raw = json.loads((attempt / "provider-response.json").read_bytes())
    assert raw["model"] == "deepseek-v4.1-flash" and raw["usage"]["credit"] == 0.02
    assert recover_completion(attempt, "synthetic") == result


def test_transport_change_does_not_repeat_existing_attempt(tmp_path: Path) -> None:
    def unexpected(_request: httpx.Request) -> httpx.Response:
        pytest.fail("A frozen attempt must not be retransmitted")

    attempt = tmp_path / "network/00/attempt.json"
    attempt.parent.mkdir(parents=True)
    attempt.write_text("{}", encoding="utf-8")
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        transport = RetryingCompletion(client, HttpRetryPolicy(enabled=True), ModelRoute.WORKBUDDY)
        with pytest.raises(WorkflowError, match="旧通道已记账"):
            transport.send(PAYLOAD, KEY, "synthetic", tmp_path)
        (tmp_path / "network/model-route.txt").write_text("workbuddy", encoding="utf-8")
        with pytest.raises(WorkflowError, match="既有模型调用不能切换"):
            RetryingCompletion(client, HttpRetryPolicy(enabled=True)).send(
                PAYLOAD, KEY, "synthetic", tmp_path
            )


def test_credential_reads_only_gateway_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-official-key")
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps({"api_key": KEY}), encoding="utf-8")
    assert read_workbuddy_key(path) == KEY
    with pytest.raises(WorkflowError, match="网关凭据不可用"):
        read_workbuddy_key(tmp_path / "missing.json")


def test_gateway_unexpected_model_does_not_pass_workflow_validation() -> None:
    with pytest.raises(WorkflowError, match="回执模型"):
        ModelRoute.WORKBUDDY.receipt_model("other-model")
