from datetime import timedelta

import pytest
from conftest import Clock
from fastapi.testclient import TestClient

from youmo_workflow.api import create_app
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import SessionInfo
from youmo_workflow.domain.scan_plan import ScanPlanUpdate
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.scan_plan import SCAN_KINDS, ScanPlanService
from youmo_workflow.features.tasks import add_task
from youmo_workflow.orchestration.scan_scheduler import ScanScheduler
from youmo_workflow.runtime import Runtime


def test_scans_repeat_every_thirty_minutes_without_production(
    runtime: Runtime, clock: Clock
) -> None:
    scheduler = ScanScheduler(runtime.factory, runtime.policy, clock)
    scheduler.tick()
    assert len(runtime.tasks.list_tasks()) == 1
    task = runtime.tasks.claim(SCAN_KINDS)
    assert task
    runtime.tasks.finish(task)
    clock.at += timedelta(minutes=29)
    scheduler.tick()
    assert len(runtime.tasks.list_tasks()) == 1
    clock.at += timedelta(minutes=1)
    scheduler.tick()
    assert len(runtime.tasks.list_tasks()) == 2
    assert not runtime.batches.list_batches()


def test_config_hot_load_and_restart_keeps_next_scan(runtime: Runtime, clock: Clock) -> None:
    service = ScanPlanService(runtime.factory, clock)
    saved = service.update(ScanPlanUpdate(interval_minutes=45, revision=0))
    assert saved.next_run_at == stamp(clock() + timedelta(minutes=45))
    ScanScheduler(runtime.factory, runtime.policy, clock).tick()
    assert not runtime.tasks.list_tasks()
    clock.at += timedelta(minutes=45)
    ScanScheduler(runtime.factory, runtime.policy, clock).tick()
    assert len(runtime.tasks.list_tasks()) == 1
    with pytest.raises(WorkflowError, match="最新配置"):
        service.update(ScanPlanUpdate(revision=0))
    service.update(ScanPlanUpdate(enabled=False, revision=1))
    task = runtime.tasks.claim(SCAN_KINDS)
    assert task
    runtime.tasks.finish(task)
    clock.at += timedelta(days=1)
    ScanScheduler(runtime.factory, runtime.policy, clock).tick()
    assert len(runtime.tasks.list_tasks()) == 1


def test_scanner_claims_only_metadata_while_production_is_running(
    runtime: Runtime, clock: Clock
) -> None:
    with runtime.factory.begin() as db:
        add_task(db, "editing", "long-production", stamp(clock()))
    production = runtime.tasks.claim(("editing",))
    assert production
    ScanScheduler(runtime.factory, runtime.policy, clock).tick()
    scan = runtime.tasks.claim(SCAN_KINDS)
    assert scan and scan.kind == "sync_source"
    runtime.tasks.finish(scan)
    assert runtime.tasks.claim(SCAN_KINDS) is None
    assert not runtime.batches.list_batches()


def test_scan_settings_api_is_independent_and_csrf_guarded(runtime: Runtime) -> None:
    with TestClient(create_app(runtime)) as client:
        assert client.get("/api/v1/scan-plan").status_code == 200
        body = {"enabled": True, "interval_minutes": 60, "revision": 0}
        assert client.put("/api/v1/scan-plan", json=body).status_code == 403
        token = SessionInfo.model_validate_json(client.get("/api/v1/session").content).csrf_token
        assert (
            client.put(
                "/api/v1/scan-plan", json=body, headers={"x-workflow-token": token}
            ).status_code
            == 200
        )
        assert not runtime.tasks.list_tasks() and not runtime.batches.list_batches()
