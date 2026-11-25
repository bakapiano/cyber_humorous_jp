from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from youmo_workflow.api import create_app
from youmo_workflow.domain.models import SessionInfo
from youmo_workflow.runtime import Runtime


def test_login_management_is_csrf_guarded(runtime: Runtime) -> None:
    with TestClient(create_app(runtime)) as client:
        for path in ["pairing", "verify"]:
            assert client.post(f"/api/v1/youtube/{path}").status_code == 403
        assert client.delete("/api/v1/youtube/connection").status_code == 403
        token = SessionInfo.model_validate_json(client.get("/api/v1/session").content).csrf_token
        assert (
            client.post(
                "/api/v1/youtube/pairing",
                headers={
                    "x-workflow-token": token,
                    "origin": "https://evil.example",
                },
            ).status_code
            == 403
        )


def test_public_login_api_validates_and_drops_no_secret_fields(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def secret_response(*_args: object) -> bytes:
        return b'{"status":"connected","cookie":"must-not-leak"}'

    monkeypatch.setattr("youmo_workflow.api_youtube.node_request", secret_response)
    with TestClient(create_app(runtime)) as client:
        response = client.get("/api/v1/youtube/connection")
        assert response.status_code == 409
        assert "must-not-leak" not in response.text


def test_recommendation_activation_hot_loads_and_fails_closed(runtime: Runtime) -> None:
    marker = runtime.settings.root / "data/youtube-connection.json"
    assert not runtime.settings.recommendations_enabled
    marker.write_text('{"enabled":true}', encoding="utf-8")
    assert runtime.settings.recommendations_enabled
    marker.write_text('{"enabled":false}', encoding="utf-8")
    assert not runtime.settings.recommendations_enabled
    marker.write_text("corrupt", encoding="utf-8")
    assert not runtime.settings.recommendations_enabled


def test_extension_download_only_contains_build_artifacts(runtime: Runtime) -> None:
    directory = runtime.settings.root / "extensions/youtube-login/dist"
    directory.mkdir(parents=True)
    (directory / "manifest.json").write_text('{"manifest_version":3}', encoding="utf-8")
    (directory / "background.js").write_text("// synthetic build artifact", encoding="utf-8")
    (directory / "excluded.txt").write_text("must-not-be-packaged", encoding="utf-8")
    with TestClient(create_app(runtime)) as client:
        response = client.get("/api/v1/youtube/extension.zip")
        assert response.status_code == 200
        with ZipFile(BytesIO(response.content)) as archive:
            assert sorted(archive.namelist()) == ["background.js", "manifest.json"]


def test_pairing_proxy_returns_only_the_one_time_code(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []

    async def pairing_response(_path: object, method: str, endpoint: str) -> bytes:
        calls.append((method, endpoint))
        return (
            b'{"code":"synthetic","expires_at":"2026-11-06T10:00:00Z","extension_id":"public-id"}'
        )

    monkeypatch.setattr("youmo_workflow.api_youtube.node_request", pairing_response)
    with TestClient(create_app(runtime)) as client:
        token = SessionInfo.model_validate_json(client.get("/api/v1/session").content).csrf_token
        response = client.post("/api/v1/youtube/pairing", headers={"x-workflow-token": token})
        assert response.status_code == 200
        assert calls == [("POST", "/auth/pairing")]
        assert "grant" not in response.text
