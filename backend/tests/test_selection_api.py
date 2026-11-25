from conftest import Clock
from fastapi.testclient import TestClient
from selection_support import discovery_items

from youmo_workflow.api import create_app
from youmo_workflow.domain.models import Policy, SessionInfo
from youmo_workflow.domain.time import stamp
from youmo_workflow.runtime import Runtime


def test_initial_kickoff_is_csrf_guarded_and_does_not_run_models_in_api(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(2), stamp(clock()))
    batch = runtime.batches.create(Policy())
    # Simulate the legacy unimplemented task's prior failure; do not invoke a provider.
    task = runtime.tasks.claim()
    assert task is not None
    from youmo_workflow.domain.errors import WorkflowError

    runtime.tasks.fail(task, WorkflowError("SELECTION_NOT_IMPLEMENTED", "legacy"))
    runtime.batches.set_result(batch.id, "failed")
    with TestClient(create_app(runtime)) as client:
        path = f"/api/v1/batches/{batch.id}/initial-selection"
        assert client.post(path).status_code == 403
        token = SessionInfo.model_validate_json(client.get("/api/v1/session").content).csrf_token
        first = client.post(path, headers={"x-workflow-token": token})
        second = client.post(path, headers={"x-workflow-token": token})
        assert first.status_code == second.status_code == 202
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["status"] == "pending"
        results = client.get(f"/api/v1/batches/{batch.id}/selection-results")
        assert results.status_code == 200 and results.json()["initial"] is None
        assert str(runtime.settings.root) not in first.text + results.text


def test_results_endpoint_rejects_missing_batch(runtime: Runtime) -> None:
    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/v1/batches/absent/selection-results").status_code == 409
