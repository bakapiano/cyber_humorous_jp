import pytest
from conftest import Clock
from sqlalchemy import func, select
from test_backlog import snapshot, stop_batch

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.orchestration.backlog_control import SETTING, BacklogController, BacklogRun
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting


def test_controller_consumes_each_completed_full_slice_once(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = snapshot(runtime, clock, monkeypatch)
    control = BacklogController(runtime)
    assert not control.tick()
    initial = control.start([path], True)
    assert control.start([path], True) == initial
    for expected in (1, 2):
        assert control.tick()
        assert control.tick()
        with runtime.factory() as db:
            assert db.scalar(select(func.count()).select_from(Batch)) == expected
            batch = db.scalar(select(Batch).where(Batch.status == "queued"))
            assert batch and batch.execution_json
            assert BatchExecution.model_validate_json(batch.execution_json).skip_review_rejections
        stop_batch(runtime, batch.id)
        with runtime.factory.begin() as db:
            stopped = db.get(Batch, batch.id)
            assert stopped
            stopped.status = "completed_with_skips"
            for task in db.scalars(select(Task).where(Task.batch_id == batch.id)):
                task.status, task.error_code = "succeeded", None
    assert control.tick()
    assert not control.tick()
    with runtime.factory() as db:
        saved = db.get(WorkflowSetting, SETTING)
        assert saved
        final = BacklogRun.model_validate_json(saved.value_json)
        assert final.status == "no_full_slices_remaining" and not final.enabled
        assert all(batch.status == "completed_with_skips" for batch in db.scalars(select(Batch)))


def test_controller_stops_new_slices_after_nonreview_failure(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = snapshot(runtime, clock, monkeypatch)
    control = BacklogController(runtime)
    control.start([path], True)
    control.tick()
    with runtime.factory() as db:
        batch = db.scalar(select(Batch))
        assert batch
    stop_batch(runtime, batch.id)
    control.tick()
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(Batch)) == 1
        saved = db.get(WorkflowSetting, SETTING)
        assert saved
        run = BacklogRun.model_validate_json(saved.value_json)
        assert not run.enabled and run.last_error == "BACKLOG_BATCH_FAILED"


def test_controller_freezes_scope_and_stops_on_changed_manifest(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = snapshot(runtime, clock, monkeypatch)
    control = BacklogController(runtime)
    control.start([path], True)
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert control.tick()
    with runtime.factory() as db:
        saved = db.get(WorkflowSetting, SETTING)
        assert saved
        state = BacklogRun.model_validate_json(saved.value_json)
        assert not state.enabled and state.last_error == "BACKLOG_CHANGED"
        assert not db.scalar(select(Batch.id))
    with pytest.raises(WorkflowError, match="不同冻结范围"):
        control.start([path], True)


def test_diagnosed_outage_allows_next_slice_but_preserves_failures(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = snapshot(runtime, clock, monkeypatch)
    control = BacklogController(runtime)
    control.start([path], True)
    control.tick()
    with runtime.factory() as db:
        first = db.scalar(select(Batch))
        assert first
    stop_batch(runtime, first.id)
    control.tick()
    resumed = control.resume_after_diagnosis(
        {first.id: "Provider recovered; retries exhausted."}, True
    )
    assert first.id not in resumed.preexisting_batch_ids
    assert resumed.enabled
    control.tick()
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(Batch)) == 2
        failed = db.get(Batch, first.id)
        assert failed and failed.status == "failed"
        second = db.scalar(select(Batch).where(Batch.id != first.id))
        assert second
    stop_batch(runtime, second.id)
    control.tick()
    with runtime.factory() as db:
        saved = db.get(WorkflowSetting, SETTING)
        assert saved
        stopped = BacklogRun.model_validate_json(saved.value_json)
        assert not stopped.enabled and stopped.last_error == "BACKLOG_BATCH_FAILED"


def test_diagnosis_does_not_accept_missing_or_running_batches(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = snapshot(runtime, clock, monkeypatch)
    control = BacklogController(runtime)
    control.start([path], True)
    control.tick()
    with runtime.factory() as db:
        batch = db.scalar(select(Batch))
        assert batch
    for identifier in ("missing", batch.id):
        with pytest.raises(WorkflowError, match="本轮失败批次"):
            control.resume_after_diagnosis({identifier: "diagnosed"}, True)
