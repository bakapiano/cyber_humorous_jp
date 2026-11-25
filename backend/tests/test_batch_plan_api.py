from conftest import Clock
from fastapi.testclient import TestClient
from selection_support import discovery_items
from test_batch_trigger import manual

from youmo_workflow.api import create_app
from youmo_workflow.domain.batch_plan import BatchPlanState
from youmo_workflow.domain.models import BatchDTO, SessionInfo
from youmo_workflow.domain.time import stamp
from youmo_workflow.runtime import Runtime


def test_plan_api_and_trigger_are_guarded_persisted_and_do_not_execute(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.settings.bilibili_credentials.parent.mkdir(parents=True)
    runtime.settings.bilibili_credentials.write_text('{"synthetic":true}', encoding="utf-8")
    runtime.settings.bilibili_credentials.with_name("verified.json").write_text(
        '{"synthetic":true}', encoding="utf-8"
    )
    runtime.sources.ingest("tbsnewsdig", discovery_items(55), stamp(clock()))
    with TestClient(create_app(runtime)) as client:
        initial = BatchPlanState.model_validate_json(client.get("/api/v1/batch-plan").content)
        assert not initial.auto_enabled and initial.revision == 0
        body = {"batch_size": 50, "publications_per_batch": 2, "revision": 0}
        assert client.put("/api/v1/batch-plan", json=body).status_code == 403
        token = SessionInfo.model_validate_json(client.get("/api/v1/session").content).csrf_token
        headers = {"x-workflow-token": token}
        saved = client.put("/api/v1/batch-plan", json=body, headers=headers)
        assert saved.status_code == 200 and not runtime.tasks.list_tasks()
        assert client.put("/api/v1/batch-plan", json=body, headers=headers).status_code == 409
        request = manual(1).model_dump(mode="json")
        response = client.post("/api/v1/production-batches", json=request, headers=headers)
        assert response.status_code == 202
        result = BatchDTO.model_validate_json(response.content)
        assert result.candidate_count == 50 and result.publications_per_batch == 2
        again = client.post("/api/v1/production-batches", json=request, headers=headers)
        assert BatchDTO.model_validate_json(again.content).id == result.id
        assert all(task.status == "pending" for task in runtime.tasks.list_tasks())
        assert not runtime.publications.list_publications()
