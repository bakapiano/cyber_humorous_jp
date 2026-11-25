import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from conftest import Clock

from youmo_workflow.adapters.download_retry import download_with_retry, retryable_tls
from youmo_workflow.domain.errors import WorkflowError

TLS = "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"


def test_tls_retry_succeeds_after_five_and_fifteen_seconds(tmp_path: Path, clock: Clock) -> None:
    delays: list[float] = []

    def wait(seconds: float) -> None:
        delays.append(seconds)
        clock.at += timedelta(seconds=seconds)

    operation = Mock(side_effect=[RuntimeError(TLS), RuntimeError(TLS), "ok"])
    assert download_with_retry(tmp_path, "abcdefghijk", operation, wait=wait, clock=clock) == "ok"
    assert delays == [5, 15] and operation.call_count == 3
    data = json.loads((tmp_path / "download-transport.json").read_text())
    assert [a["status"] for a in data["attempts"]] == ["tls_eof", "tls_eof", "succeeded"]


def test_tls_exhaustion_survives_restart_and_does_not_log_private_text(
    tmp_path: Path, clock: Clock
) -> None:
    operation = Mock(side_effect=RuntimeError(TLS + " https://example.test?signature=secret"))
    with pytest.raises(RuntimeError):
        download_with_retry(tmp_path, "abcdefghijk", operation, wait=lambda _: None, clock=clock)
    raw = (tmp_path / "download-transport.json").read_text()
    assert operation.call_count == 3 and "secret" not in raw and "https" not in raw
    with pytest.raises(WorkflowError, match="次数已用尽"):
        download_with_retry(tmp_path, "abcdefghijk", operation, wait=lambda _: None, clock=clock)
    assert operation.call_count == 3


@pytest.mark.parametrize(
    "message",
    [
        "HTTP Error 403 " + TLS,
        "Sign in to confirm you are not a bot " + TLS,
        "captcha " + TLS,
        "CERTIFICATE_VERIFY_FAILED " + TLS,
        "cookie expired " + TLS,
        "requested format is not available",
        "connection refused",
        "TLS handshake protocol error",
    ],
)
def test_other_failures_never_retry(tmp_path: Path, clock: Clock, message: str) -> None:
    operation = Mock(side_effect=RuntimeError(message))
    wait = Mock()
    with pytest.raises(RuntimeError):
        download_with_retry(tmp_path, "abcdefghijk", operation, wait=wait, clock=clock)
    operation.assert_called_once()
    wait.assert_not_called()
    assert not retryable_tls(WorkflowError("SOURCE_LOGIN_REQUIRED", TLS))


def test_restart_resumes_persisted_delay_not_new_budget(tmp_path: Path, clock: Clock) -> None:
    operation = Mock(side_effect=RuntimeError(TLS))
    with pytest.raises(KeyboardInterrupt):
        download_with_retry(
            tmp_path,
            "abcdefghijk",
            operation,
            wait=Mock(side_effect=KeyboardInterrupt),
            clock=clock,
        )
    operation.assert_called_once()
    clock.at += timedelta(seconds=2)
    wait = Mock()
    resumed = Mock(return_value="ok")
    assert download_with_retry(tmp_path, "abcdefghijk", resumed, wait=wait, clock=clock) == "ok"
    wait.assert_called_once_with(3)
    assert len(json.loads((tmp_path / "download-transport.json").read_text())["attempts"]) == 2


def test_unknown_interruption_does_not_repeat_and_wrong_source_is_rejected(
    tmp_path: Path, clock: Clock
) -> None:
    operation = Mock(side_effect=KeyboardInterrupt)
    with pytest.raises(KeyboardInterrupt):
        download_with_retry(tmp_path, "abcdefghijk", operation, clock=clock)
    with pytest.raises(WorkflowError, match="未确认"):
        download_with_retry(tmp_path, "abcdefghijk", operation, clock=clock)
    with pytest.raises(WorkflowError, match="身份不一致"):
        download_with_retry(tmp_path, "different", operation, clock=clock)
    operation.assert_called_once()
