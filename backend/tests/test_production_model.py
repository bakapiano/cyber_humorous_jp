from pathlib import Path

import httpx
import pytest

from youmo_workflow.adapters.production_model import ProductionModel
from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.production_archive import archive_payload


def test_production_response_reused_and_changed_inputs_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-only-not-a-real-key")
    calls: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"synthetic":true}',
                            "reasoning_content": "synthetic reasoning",
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path)
        request = GenerationRequest(content_id="synthetic", stage="title_v004", prompt="test")
        assert model.generate(request) == model.generate(request)
        assert model.cached(request) == '{"synthetic":true}'
        assert len(calls) == 1
        folder = tmp_path / "production/synthetic/title_v004"
        (folder / "response.json").unlink()
        assert model.generate(request) == '{"synthetic":true}'
        assert len(calls) == 1
        assert '"max_tokens":65536' in (folder / "payload.json").read_text(encoding="utf-8")
        assert "synthetic reasoning" in (folder / "provider-response.json").read_text(
            encoding="utf-8"
        )
        assert not archive_payload(folder / "request.json")
        (folder / "payload.json").unlink()
        assert archive_payload(folder / "request.json")
        (folder / "fingerprint.txt").write_text("wrong", encoding="utf-8")
        with pytest.raises(WorkflowError, match="指纹不匹配"):
            archive_payload(folder / "request.json")
        with pytest.raises(WorkflowError, match="冻结输入"):
            model.generate(request.model_copy(update={"prompt": "changed"}))
    for path in (tmp_path / "production").rglob("*.json"):
        assert "sk-test-only" not in path.read_text(encoding="utf-8")


def test_model_timeout_never_reissues_a_paid_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ReadTimeout("synthetic")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path)
        request = GenerationRequest(content_id="synthetic", stage="editing_v002", prompt="test")
        for _ in range(2):
            with pytest.raises(WorkflowError):
                model.generate(request)
    assert len(calls) == 1


def test_http_error_is_archived_without_reissuing_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-synthetic")
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, content=b'{\n"error":"synthetic upstream unavailable"\n}')

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path)
        request = GenerationRequest(content_id="synthetic", stage="editing_v003", prompt="test")
        with pytest.raises(WorkflowError, match="HTTP 503"):
            model.generate(request)
        with pytest.raises(WorkflowError, match="归档响应状态"):
            model.generate(request)
    folder = tmp_path / "production/synthetic/editing_v003"
    assert (
        folder / "provider-response.json"
    ).read_bytes() == b'{\n"error":"synthetic upstream unavailable"\n}'
    assert '"status_code":503' in (folder / "provider-http.json").read_text(encoding="utf-8")
    assert len(calls) == 1 and not (folder / "response.json").exists()
