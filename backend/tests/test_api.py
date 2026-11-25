from conftest import Clock, sample_video
from fastapi.testclient import TestClient

from youmo_workflow.api import create_app
from youmo_workflow.domain.models import SessionInfo
from youmo_workflow.domain.time import stamp
from youmo_workflow.runtime import Runtime


def test_api_is_local_and_csrf_guarded(runtime: Runtime) -> None:
    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/health", headers={"host": "evil.example"}).status_code == 403
        assert client.post("/api/v1/production-batches").status_code == 403
        token = SessionInfo.model_validate_json(client.get("/api/v1/session").content).csrf_token
        assert (
            client.post(
                "/api/v1/production-batches",
                headers={"x-workflow-token": token, "origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/production-batches", headers={"x-workflow-token": token}
            ).status_code
            == 422
        )


def test_public_api_does_not_expose_paths(runtime: Runtime, clock: Clock) -> None:
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    with TestClient(create_app(runtime)) as client:
        for path in ["/api/v1/sources", "/api/v1/videos", "/api/v1/dashboard"]:
            response = client.get(path)
            assert response.status_code == 200
            assert str(runtime.settings.root) not in response.text
            assert "api_key" not in response.text.lower()


def test_openapi_export_has_no_side_effects(runtime: Runtime) -> None:
    schema = create_app(runtime).openapi()
    assert "/api/v1/production-batches" in schema["paths"]
    assert not runtime.batches.list_batches()


def test_unknown_artifact_cannot_preview(runtime: Runtime) -> None:
    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/v1/artifacts/not-a-file/preview").status_code == 409
