import json
from pathlib import Path

import httpx
import pytest
from PIL import Image
from pydantic import BaseModel

from youmo_workflow.adapters.production_files import digest
from youmo_workflow.adapters.production_model import ProductionModel
from youmo_workflow.adapters.selection_models import SavedCompletion
from youmo_workflow.domain.content import GenerationRequest, ModelImage
from youmo_workflow.domain.errors import WorkflowError


class StructuredResult(BaseModel):
    value: int


def response(content: str, reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "deepseek-flash",
            "choices": [{"finish_reason": reason, "message": {"content": content}}],
        },
    )


@pytest.mark.parametrize("repairs", [0, 1, 2])
def test_format_corrections_keep_history_and_reuse_all_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repairs: int
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    sent: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode())
        return response('{"value":"wrong"}' if len(sent) <= repairs else '{"value":7}')

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        request = GenerationRequest(
            content_id="test", stage="title_v004", prompt="Original source and schema"
        )
        for _ in range(2):
            model = ProductionModel(client, tmp_path / "unused", tmp_path)
            assert (
                model.generate_checked(request, StructuredResult.model_validate_json)
                == '{"value":7}'
            )
    assert len(sent) == repairs + 1
    for index, body in enumerate(sent):
        payload = json.loads(body)
        assert payload["messages"][0]["content"] == request.prompt
        assert len(payload["messages"]) == 1 + 2 * index
        if index:
            assert payload["messages"][-2]["content"] == '{"value":"wrong"}'
            assert "int_parsing" in payload["messages"][-1]["content"]
        assert payload["reasoning_effort"] == "high" and "sk-test" not in body
    directory = tmp_path / "production/test/title_v004"
    assert len(list(directory.rglob("attempt.json"))) == repairs + 1
    assert len(list(directory.glob("validation-*.json"))) == repairs + 1
    assert (directory / "accepted.json").exists()


def test_two_bad_corrections_exhaust_budget_permanently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return response("not JSON")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        request = GenerationRequest(content_id="test", stage="editing_v003", prompt="source")
        for _ in range(3):
            with pytest.raises(WorkflowError) as error:
                ProductionModel(client, tmp_path / "unused", tmp_path).generate_checked(
                    request, StructuredResult.model_validate_json
                )
            assert error.value.code == "MODEL_FORMAT_RETRIES_EXHAUSTED"
    assert len(calls) == 3


@pytest.mark.parametrize("mode", ["http", "timeout", "content_filter"])
def test_nonformat_failures_never_retransmit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if mode == "timeout":
            raise httpx.ReadTimeout("synthetic")
        if mode == "http":
            return httpx.Response(503, text="synthetic unavailable")
        return response("", "content_filter")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        request = GenerationRequest(content_id="test", stage="editing_v003", prompt="source")
        model = ProductionModel(client, tmp_path / "unused", tmp_path)
        for _ in range(2):
            with pytest.raises(WorkflowError):
                model.generate_checked(request, StructuredResult.model_validate_json)
    assert len(calls) == 1


def test_uncertain_correction_cannot_be_skipped_for_next_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    sent: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode())
        if len(sent) > 1:
            raise httpx.ReadTimeout("synthetic")
        return response("wrong")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path)
        request = GenerationRequest(content_id="test", stage="editing_v003", prompt="source")
        for _ in range(2):
            with pytest.raises(WorkflowError) as error:
                model.generate_checked(request, StructuredResult.model_validate_json)
            assert error.value.code == "MODEL_ATTEMPT_UNRESOLVED"
    assert len(sent) == 2
    assert not (tmp_path / "production/test/editing_v003/corrections/02").exists()


def test_visual_constraint_retry_keeps_original_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    path = tmp_path / "frame.jpg"
    Image.new("RGB", (16, 16), "blue").save(path)
    request = GenerationRequest(
        content_id="test",
        stage="cover_vision_v003b",
        prompt="Image and normalized coordinates",
        images=[ModelImage(label="F001", path=path, sha256=digest(path))],
    )
    sent: list[str] = []

    def handle(value: httpx.Request) -> httpx.Response:
        sent.append(value.content.decode())
        return response('{"value":-1}' if len(sent) == 1 else '{"value":1}')

    def validate(raw: str) -> None:
        if StructuredResult.model_validate_json(raw).value < 0:
            raise WorkflowError(
                "PRODUCTION_VALIDATION_FAILED", "subject_box must have x1<x2 and y1<y2"
            )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        ProductionModel(client, tmp_path / "unused", tmp_path).generate_checked(request, validate)
    assert len(sent) == 2
    assert json.loads(sent[0])["messages"][0] == json.loads(sent[1])["messages"][0]
    assert "data:image/jpeg;base64," in sent[1] and "subject_box" in sent[1]


def test_wrong_response_fingerprint_never_becomes_a_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return response('{"value":1}')

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path)
        request = GenerationRequest(content_id="test", stage="title_v004", prompt="source")
        model.generate(request)
        path = tmp_path / "production/test/title_v004/response.json"
        result = SavedCompletion.model_validate_json(path.read_bytes())
        path.write_text(
            result.model_copy(update={"request_sha256": "wrong"}).model_dump_json(),
            encoding="utf-8",
        )
        with pytest.raises(WorkflowError, match="指纹不匹配"):
            model.generate_checked(request, StructuredResult.model_validate_json)
    assert len(calls) == 1
