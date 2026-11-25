import subprocess
from pathlib import Path
from typing import Never

import pytest

from youmo_workflow.adapters.bilibili_subtitles import read_credentials
from youmo_workflow.adapters.biliup import BiliupGateway
from youmo_workflow.domain.errors import WorkflowError


def test_invalid_credentials_do_not_echo_secret(tmp_path: Path) -> None:
    path = tmp_path / "private-credentials.json"
    path.write_text('{"cookie_info":{"cookies":[{"value":"SENTINEL-SECRET"}]}}', encoding="utf-8")
    with pytest.raises(WorkflowError) as error:
        read_credentials(path)
    assert "SENTINEL-SECRET" not in str(error.value)
    assert str(path) not in str(error.value)


def test_cli_timeout_does_not_echo_credential_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> Never:
        raise subprocess.TimeoutExpired(["exe", "-u", "SENTINEL-PRIVATE-PATH"], 30)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(WorkflowError) as error:
        BiliupGateway(tmp_path / "biliup.exe", tmp_path / "secret.json").inspect("BV1234567890")
    assert "SENTINEL-PRIVATE-PATH" not in str(error.value)
