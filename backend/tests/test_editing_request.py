from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from youmo_workflow.adapters.editing_request import JSON_DIRECTIVE, range_request
from youmo_workflow.adapters.production_model import ProductionModel
from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.errors import WorkflowError


class Response(BaseModel):
    value: int


def test_json_mode_protocol_is_explicit_without_changing_original_prompt() -> None:
    source = "只输出时间区间，不抄写证据。"
    request = range_request("test", source, lambda _: None)
    assert request.stage == "editing_v003_json"
    assert request.prompt == source + JSON_DIRECTIVE
    assert "json" in request.prompt.lower()


def test_existing_valid_or_truncated_response_keeps_old_stage() -> None:
    assert range_request("test", "old", lambda _: "{}").stage == "editing_v003"

    def incomplete(_request: GenerationRequest) -> str | None:
        raise WorkflowError("MODEL_OUTPUT_INVALID", "truncated")

    assert range_request("test", "old", incomplete).stage == "editing_v003"


def test_changed_input_cannot_escape_through_protocol_version() -> None:
    def changed(_request: GenerationRequest) -> str | None:
        raise WorkflowError("PRODUCTION_INPUT_CHANGED", "changed")

    with pytest.raises(WorkflowError, match="changed"):
        range_request("test", "old", changed)


def test_known_json_requirement_no_longer_hits_provider_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "输出协议" not in request.content.decode():
            return httpx.Response(
                400, json={"error": {"message": "Prompt must contain the word json"}}
            )
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": '{"value":1}'}}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path)
        old = GenerationRequest(content_id="test", stage="editing_v003", prompt="仅时间区间")
        with pytest.raises(WorkflowError):
            model.generate(old)
        old_request = (tmp_path / "production/test/editing_v003/request.json").read_bytes()
        fixed = range_request("test", old.prompt, model.cached)
        assert model.generate_checked(fixed, Response.model_validate_json) == '{"value":1}'
        assert (tmp_path / "production/test/editing_v003/request.json").read_bytes() == old_request
    assert len(calls) == 2
