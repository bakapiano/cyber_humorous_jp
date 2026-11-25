import json

import pytest
from conftest import Clock
from sqlalchemy import func, select
from test_backlog import snapshot
from test_batch_trigger import configure

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.orchestration.backlog_control import SETTING, BacklogController, BacklogRun
from youmo_workflow.orchestration.scheduler import Scheduler
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting


def predecessor(runtime: Runtime, clock: Clock, status: str) -> None:
    at = stamp(clock())
    with runtime.factory.begin() as db:
        db.add(
            Batch(
                id="old",
                local_date="2026-11-07",
                snapshot_at=at,
                status="failed",
                policy_json=runtime.policy.model_dump_json(),
                candidate_count=100,
            )
        )
        db.add(
            Task(
                id="old-task",
                unique_key="old-task",
                kind="prepare_video",
                batch_id="old",
                status=status,
                created_at=at,
                updated_at=at,
                next_run_at=at,
            )
        )
        db.add(
            WorkflowSetting(
                id="batch-ratio-change:test",
                revision=1,
                value_json=json.dumps(
                    {
                        "resume_requires_repaired_production_batches": ["old"],
                        "preserved_extra_audit_field": True,
                    }
                ),
            )
        )


@pytest.mark.parametrize("status", ["failed", "unknown", "pending", "running", "retry_wait"])
def test_ratio_audit_blocks_resume_until_predecessor_production_finishes(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    control = BacklogController(runtime)
    control.start([snapshot(runtime, clock, monkeypatch)], True)
    predecessor(runtime, clock, status)
    with pytest.raises(WorkflowError):
        control.resume_after_diagnosis({}, True)
    control.tick()
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(Batch)) == 1
        original = db.get(Task, "old-task")
        assert original and original.status == status


def test_new_manifest_empty_cursor_cannot_bypass_missing_predecessor(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = BacklogController(runtime)
    control.start([snapshot(runtime, clock, monkeypatch)], True)
    with runtime.factory.begin() as db:
        db.add(
            WorkflowSetting(
                id="batch-ratio-change:test",
                revision=1,
                value_json=json.dumps(
                    {
                        "resume_requires_repaired_production_batches": ["missing"],
                    }
                ),
            )
        )
    assert control.tick()
    with runtime.factory() as db:
        assert not db.scalar(select(Batch.id))
        saved = db.get(WorkflowSetting, SETTING)
        assert (
            saved
            and BacklogRun.model_validate_json(saved.value_json).last_error
            == "BACKLOG_BATCH_FAILED"
        )


def test_repaired_production_can_advance_while_delivery_is_rate_limited(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = BacklogController(runtime)
    control.start([snapshot(runtime, clock, monkeypatch)], True)
    predecessor(runtime, clock, "succeeded")
    with runtime.factory.begin() as db:
        at = stamp(clock())
        db.add(
            Task(
                id="publish-old",
                unique_key="publish-old",
                kind="publish",
                batch_id="old",
                status="retry_wait",
                error_code="BILI_SUBMIT_RATE_LIMIT",
                created_at=at,
                updated_at=at,
                next_run_at=at,
            )
        )
    assert control.resume_after_diagnosis({}, True).enabled
    assert control.tick()
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(Batch)) == 2
        old_upload = db.get(Task, "publish-old")
        assert old_upload and old_upload.status == "retry_wait"


def test_attention_required_run_does_not_fall_through_to_live_candidates(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    control = BacklogController(runtime)
    control.start([snapshot(runtime, clock, monkeypatch)], True)
    configure(runtime, clock, auto=True)
    with runtime.factory.begin() as db:
        saved = db.get(WorkflowSetting, SETTING)
        assert saved
        run = BacklogRun.model_validate_json(saved.value_json)
        run.enabled, run.status = False, "attention_required"
        saved.value_json = run.model_dump_json()
    scheduler = Scheduler(runtime.factory, runtime.policy, clock)
    scheduler.backlog_tick = control.tick
    scheduler.tick()
    assert not runtime.batches.list_batches()
