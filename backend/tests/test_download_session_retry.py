import http.cookiejar
import json
from pathlib import Path

import httpx
import pytest

from youmo_workflow.adapters.download_session import RETRY_DELAYS, NodeDownloadSession
from youmo_workflow.domain.errors import WorkflowError


def test_refreshing_then_network_timeout_recovers_without_secret_logs(tmp_path: Path) -> None:
    token, journal = tmp_path / "token", tmp_path / "events.jsonl"
    token.write_text("private-test-token", encoding="utf-8")
    calls: list[str] = []
    waits: list[float] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(
                503, json={"code": "SESSION_REFRESHING", "message": "secret-body"}
            )
        if len(calls) == 2:
            raise httpx.ReadTimeout("secret-network-error", request=request)
        return httpx.Response(200, json={"cookie": "SAPISID=private-cookie", "revision": "1"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        jar = http.cookiejar.CookieJar()
        NodeDownloadSession(token, client, wait=waits.append, journal=journal).load(jar)
    assert len(calls) == 3 and waits == [2, 4]
    assert next(iter(jar)).value == "private-cookie"
    text = journal.read_text(encoding="utf-8")
    for secret in ("private-test-token", "private-cookie", "secret-body", "secret-network-error"):
        assert secret not in text
    events = [json.loads(line) for line in text.splitlines()]
    assert [row["attempt"] for row in events] == [1, 2, 3]
    assert events[0]["detail"].find("HTTP 503/SESSION_REFRESHING") >= 0
    assert events[-1]["code"] == "OK"
    assert len({row["operation"] for row in events}) == 1


@pytest.mark.parametrize("status", [429, 502, 503])
def test_transient_session_reads_have_a_fixed_retry_budget(tmp_path: Path, status: int) -> None:
    token = tmp_path / "token"
    token.write_text("test-token", encoding="utf-8")
    waits: list[float] = []
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(status, json={"code": "untrusted-private-code", "message": "secret"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(WorkflowError) as error:
            NodeDownloadSession(token, client, wait=waits.append).load(http.cookiejar.CookieJar())
    assert error.value.code == "SOURCE_SESSION_RETRIES_EXHAUSTED"
    assert not error.value.retryable
    assert len(calls) == 6 and waits == list(RETRY_DELAYS)
    assert "secret" not in str(error.value) and "untrusted-private-code" not in str(error.value)


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (409, {"code": "AUTH_REQUIRED"}, "SOURCE_LOGIN_REQUIRED"),
        (401, {"code": "INTERNAL_UNAUTHORIZED"}, "SOURCE_SESSION_AUTH_FAILED"),
        (403, {"code": "BROWSER_ACCESS_DENIED"}, "SOURCE_SESSION_AUTH_FAILED"),
        (200, {"cookie": "private-cookie-invalid", "revision": "1"}, "SOURCE_SESSION_INVALID"),
        (200, {"unexpected": "private-response"}, "SOURCE_SESSION_INVALID"),
    ],
)
def test_permanent_failures_do_not_retry(
    tmp_path: Path, status: int, body: dict[str, str], expected: str
) -> None:
    token = tmp_path / "token"
    token.write_text("test-token", encoding="utf-8")
    waits: list[float] = []
    calls: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(status, json=body)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(WorkflowError) as error:
            NodeDownloadSession(token, client, wait=waits.append).load(http.cookiejar.CookieJar())
    assert error.value.code == expected
    assert len(calls) == 1 and not waits
    assert "private-" not in str(error.value)
