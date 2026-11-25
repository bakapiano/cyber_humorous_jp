import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from youmo_workflow.adapters.bilibili_login import account, import_cookie, load_export
from youmo_workflow.domain.errors import WorkflowError


def browser_export(path: Path) -> Path:
    path.write_text(
        json.dumps(
            [
                {"name": name, "value": value, "domain": ".bilibili.com"}
                for name, value in [
                    ("SESSDATA", "synthetic-session"),
                    ("bili_jct", "synthetic-csrf"),
                    ("DedeUserID", "7"),
                ]
            ]
        ),
        encoding="utf-8",
    )
    return path


def logged_in(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"code": 0, "data": {"mid": 7, "uname": "Synthetic", "isLogin": True}}
    )


def test_cookie_only_does_not_forge_app_credentials_or_delete_export(tmp_path: Path) -> None:
    source = browser_export(tmp_path / "cookie.txt")
    destination = tmp_path / "managed/session.json"
    with httpx.Client(transport=httpx.MockTransport(logged_in)) as client:
        with pytest.raises(WorkflowError) as error:
            import_cookie(source, destination, tmp_path / "cli.exe", client)
    assert error.value.code == "BILI_APP_LOGIN_REQUIRED"
    assert "synthetic-session" not in str(error.value)
    assert source.is_file() and not destination.exists()


def test_same_account_import_is_private_and_retains_source_until_caller_deletes(
    tmp_path: Path,
) -> None:
    source = browser_export(tmp_path / "cookie.txt")
    destination = tmp_path / "managed/session.json"
    destination.parent.mkdir()
    destination.write_text(
        '{"token_info":{"mid":7,"access_token":"synthetic-app"}}', encoding="utf-8"
    )
    with patch("youmo_workflow.adapters.bilibili_login.subprocess.run") as run:
        with patch("youmo_workflow.adapters.bilibili_login.protect_directory"):
            run.return_value = subprocess.CompletedProcess([], 0, b"", b"")
            with httpx.Client(transport=httpx.MockTransport(logged_in)) as client:
                result = import_cookie(source, destination, tmp_path / "cli.exe", client)
    assert result.mid == 7
    assert destination.with_name("verified.json").exists() and source.exists()
    assert not list(destination.parent.glob("import-*.json"))
    assert "synthetic-session" not in str(run.call_args)


def test_wrong_app_account_is_rejected_without_overwrite(tmp_path: Path) -> None:
    source = browser_export(tmp_path / "cookie.txt")
    destination = tmp_path / "session.json"
    previous = '{"token_info":{"mid":99,"access_token":"synthetic-app"}}'
    destination.write_text(previous, encoding="utf-8")
    with patch("youmo_workflow.adapters.bilibili_login.protect_directory"):
        with httpx.Client(transport=httpx.MockTransport(logged_in)) as client:
            with pytest.raises(WorkflowError, match="拒绝混用"):
                import_cookie(source, destination, tmp_path / "cli.exe", client)
    assert destination.read_text(encoding="utf-8") == previous


def test_invalid_cookie_error_does_not_echo_secret(tmp_path: Path) -> None:
    source = tmp_path / "cookie.txt"
    source.write_text(
        '[{"name":"SESSDATA","value":"private-value","domain":"other.example"}]', encoding="utf-8"
    )
    with pytest.raises(WorkflowError) as error:
        load_export(source)
    assert "private-value" not in str(error.value)


@pytest.mark.parametrize("code", [0, -101])
def test_logged_out_account_does_not_require_identity_fields(code: int) -> None:
    def response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": code, "data": {"isLogin": False}})

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(WorkflowError) as error:
            account(client, "synthetic-session")
    assert error.value.code == "BILI_LOGIN_REQUIRED"


def test_malformed_logged_in_identity_is_rejected_without_response_leak() -> None:
    def response(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"code": 0, "data": {"isLogin": True, "uname": "sensitive-response"}}
        )

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        with pytest.raises(WorkflowError) as error:
            account(client, "synthetic-session")
    assert error.value.code == "BILI_ACCOUNT_RESPONSE_INVALID"
    assert "sensitive-response" not in str(error.value)
