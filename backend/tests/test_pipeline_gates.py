from datetime import timedelta

import pytest
from conftest import Assets, Clock
from selection_support import discovery_items
from sqlalchemy import select
from test_batch_trigger import configure
from test_publications import manifest

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.pipeline_gates import active_holds, release_hold
from youmo_workflow.features.tasks import TaskService, add_task
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Publication, Task, WorkflowSetting


@pytest.mark.parametrize(
    "case",
    [
        ("packaging", "MODEL_HTTP_ERROR", "模型返回 HTTP 402", "editing", "publish"),
        ("initial_selection", "SELECTION_KEY_MISSING", "synthetic", "prepare_video", "sync_source"),
        ("prepare_video", "SOURCE_LOGIN_REQUIRED", "synthetic", "prepare_video", "editing"),
        ("editing", "DISK_SPACE_LOW", "synthetic", "packaging", "cleanup_publication"),
        ("publish", "BILI_LOGIN_REQUIRED", "synthetic", "publish", "packaging"),
    ],
)
def test_dependency_holds_only_affected_queues_across_restart(
    runtime: Runtime, clock: Clock, case: tuple[str, str, str, str, str]
) -> None:
    kind, code, message, blocked, allowed = case
    with runtime.factory.begin() as db:
        add_task(db, kind, "failed-dependency", stamp(clock()))
    task = runtime.tasks.claim((kind,))
    assert task
    assert runtime.tasks.fail(task, WorkflowError(code, message))
    with runtime.factory.begin() as db:
        add_task(db, blocked, "held", stamp(clock()))
        add_task(db, allowed, "independent", stamp(clock()))
    restarted = TaskService(runtime.factory, clock)
    assert restarted.claim((blocked,)) is None
    independent = restarted.claim((allowed,))
    assert independent and independent.unique_key == "independent"
    with runtime.factory.begin() as db:
        holds = active_holds(db)
        assert len(holds) == 1 and holds[0].task_id == task.id
        release_hold(db, holds[0].scope, "synthetic dependency repaired", clock())
    assert restarted.claim((blocked,)) is not None
    with runtime.factory() as db:
        original = db.get(Task, task.id)
        assert original and original.status == "failed" and original.attempts == 1
        assert db.scalar(
            select(WorkflowSetting.id).where(WorkflowSetting.id.like("pipeline_hold:history:%"))
        )


def test_bad_video_does_not_create_dependency_hold(runtime: Runtime, clock: Clock) -> None:
    with runtime.factory.begin() as db:
        add_task(db, "editing", "bad-output", stamp(clock()))
    task = runtime.tasks.claim(("editing",))
    assert task
    runtime.tasks.fail(task, WorkflowError("MODEL_FORMAT_RETRIES_EXHAUSTED", "synthetic"))
    with runtime.factory() as db:
        assert not active_holds(db)


def test_disk_guard_prevents_paid_selection_admission_without_resetting_plan(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(100), stamp(clock()))
    monkeypatch.setattr("youmo_workflow.features.production_buffer.disk_ready", lambda: False)
    monkeypatch.setattr("youmo_workflow.features.batch_plan.disk_ready", lambda: False)
    assert BatchTriggerService(runtime.factory, runtime.policy, clock).trigger() is None
    state = BatchPlanService(runtime.factory, runtime.policy, clock).state()
    assert state.auto_enabled and state.automatic_blocked and state.eligible_count == 100
    assert not runtime.tasks.list_tasks()


def test_interrupted_upload_is_quarantined_without_blocking_next_item(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    with runtime.factory.begin() as db:
        publication = db.get(Publication, prepared.id)
        assert publication
        publication.status = "submitting"
        task = add_task(db, "publish", "interrupted-upload", stamp(clock()))
        task.input_json = FinalizePrivate(
            publication_id=prepared.id, manifest_hash=prepared.manifest_hash
        ).model_dump_json()
    claimed = runtime.tasks.claim(("publish",))
    assert claimed
    clock.at += timedelta(minutes=121)
    assert runtime.tasks.recover() == 1
    with runtime.factory.begin() as db:
        publication = db.get(Publication, prepared.id)
        task = db.get(Task, claimed.id)
        assert publication and publication.status == "unknown" and not publication.remote_id
        assert task and task.status == "unknown" and task.attempts == 1
        add_task(db, "publish", "unrelated-upload", stamp(clock()))
    next_task = runtime.tasks.claim(("publish",))
    assert next_task and next_task.unique_key == "unrelated-upload"
    with pytest.raises(WorkflowError):
        runtime.tasks.retry(claimed.id)


def test_exhausted_task_retry_never_resets_history(runtime: Runtime, clock: Clock) -> None:
    with runtime.factory.begin() as db:
        row = add_task(db, "editing", "exhausted", stamp(clock()))
        row.max_attempts = 1
    task = runtime.tasks.claim(("editing",))
    assert task
    runtime.tasks.fail(task, WorkflowError("MODEL_FORMAT_RETRIES_EXHAUSTED", "synthetic"))
    with pytest.raises(WorkflowError) as error:
        runtime.tasks.retry(task.id)
    assert error.value.code == "RETRY_BUDGET_EXHAUSTED"
    with runtime.factory() as db:
        stored = db.get(Task, task.id)
        assert stored and stored.attempts == 1 and stored.status == "failed"
