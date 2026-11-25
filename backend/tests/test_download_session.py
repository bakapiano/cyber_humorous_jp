import http.cookiejar
from pathlib import Path

import httpx
import pytest

from youmo_workflow.adapters.download_session import (
    DownloadSession,
    NodeDownloadSession,
    load_header,
)
from youmo_workflow.domain.errors import WorkflowError


def test_managed_download_session_is_memory_only_and_hot_loaded(tmp_path: Path) -> None:
    token = tmp_path / "internal-token"
    token.write_text("synthetic-service-token", encoding="utf-8")
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.host == "127.0.0.1"
        assert request.headers["authorization"] == "Bearer synthetic-service-token"
        return httpx.Response(
            200, json={"cookie": f"SAPISID=synthetic-{calls}", "revision": str(calls)}
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        loader = NodeDownloadSession(token, client)
        for expected in ["synthetic-1", "synthetic-2"]:
            jar = http.cookiejar.CookieJar()
            loader.load(jar)
            assert [(row.domain, row.name, row.value) for row in jar] == [
                (".youtube.com", "SAPISID", expected)
            ]
    assert list(tmp_path.iterdir()) == [token]
    assert "secret" not in repr(
        DownloadSession.model_validate({"cookie": "secret", "revision": "1"})
    )


def test_unavailable_session_never_falls_back_or_exposes_remote_details(tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("synthetic", encoding="utf-8")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(409, text="private-response"))
    ) as client:
        with pytest.raises(WorkflowError) as error:
            NodeDownloadSession(token, client).load(http.cookiejar.CookieJar())
    assert error.value.code == "SOURCE_SESSION_UNAVAILABLE"
    assert "private-response" not in str(error.value)


@pytest.mark.parametrize(
    "header", ["SAPISID=secret\nother", "SAPISID=a; SAPISID=b", "bad cookie", ""]
)
def test_invalid_headers_are_redacted(header: str) -> None:
    with pytest.raises(WorkflowError) as error:
        load_header(http.cookiejar.CookieJar(), header)
    assert "secret" not in str(error.value)
