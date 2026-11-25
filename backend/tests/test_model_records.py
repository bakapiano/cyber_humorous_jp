from pathlib import Path

import httpx
import pytest

from youmo_workflow.adapters.model_records import (
    http_record,
    recover_completion,
    request_record,
    response_record,
)
from youmo_workflow.domain.errors import WorkflowError


def test_model_archive_keeps_parameters_and_full_response(tmp_path: Path) -> None:
    directory = request_record(
        tmp_path, ("a" * 64, "b" * 64), {"model": "synthetic", "max_tokens": 123}
    )
    assert directory
    body = '{"choices":[{"message":{"content":"answer","reasoning_content":"reason"}}]}'
    response_record(directory, body, "sk-test")
    assert (directory / "provider-response.json").read_text(encoding="utf-8") == body
    assert '"max_tokens":123' in (directory / "payload.json").read_text(encoding="utf-8")
    with pytest.raises(WorkflowError):
        request_record(tmp_path, ("../outside", "b" * 64), {})


def test_private_model_response_never_reaches_archive(tmp_path: Path) -> None:
    with pytest.raises(WorkflowError) as error:
        response_record(tmp_path, "secret sk-private-value", "sk-private-value")
    assert "sk-private-value" not in str(error.value)
    assert not list(tmp_path.iterdir())


def test_raw_http_archive_preserves_bytes_and_checks_hash(tmp_path: Path) -> None:
    body = b'{\n"model":"test",\n"choices":[{"finish_reason":"stop","message":{"content":"ok"}}]\n}'
    http_record(tmp_path, httpx.Response(200, content=body), "sk-test")
    assert (tmp_path / "provider-response.json").read_bytes() == body
    result = recover_completion(tmp_path, "a" * 64)
    assert result and result.content == "ok"
    (tmp_path / "provider-response.json").write_bytes(body + b" ")
    with pytest.raises(WorkflowError, match="哈希无效"):
        recover_completion(tmp_path, "a" * 64)


def test_escaped_private_response_is_not_archived(tmp_path: Path) -> None:
    body = '{"message":"\\u0073k-private-value"}'
    with pytest.raises(WorkflowError, match="私有信息"):
        http_record(tmp_path, httpx.Response(200, text=body), "sk-private-value")
    assert not list(tmp_path.iterdir())
