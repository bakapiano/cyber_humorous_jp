import pytest
from conftest import Clock
from selection_support import discovery_items
from sqlalchemy import select

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.initial_selection import enqueue_initial
from youmo_workflow.features.legacy_backlog import release_unstarted_batches
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task


def test_release_only_unimplemented_history_keeps_snapshot_and_disables_old_retry(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(55), stamp(clock()))
    batch = runtime.batches.create(Policy())
    original = runtime.batches.detail(batch.id)
    runtime.batches.set_result(
        batch.id, "failed", WorkflowError("SELECTION_NOT_IMPLEMENTED", "未实现")
    )
    with runtime.factory.begin() as db:
        task = db.scalar(select(Task).where(Task.batch_id == batch.id))
        assert task
        task.status, task.error_code = "failed", "SELECTION_NOT_IMPLEMENTED"
        task_id = task.id
    assert BatchPlanService(runtime.factory, runtime.policy, clock).state().eligible_count == 0
    assert release_unstarted_batches(runtime.factory) == [batch.id]
    assert release_unstarted_batches(runtime.factory) == []
    assert BatchPlanService(runtime.factory, runtime.policy, clock).state().eligible_count == 55
    assert runtime.batches.detail(batch.id).items == original.items
    assert runtime.batches.detail(batch.id).policy == original.policy
    with pytest.raises(WorkflowError, match="只读"):
        enqueue_initial(runtime.factory, batch.id, clock)
    with pytest.raises(WorkflowError, match="只读"):
        runtime.tasks.retry(task_id)


def test_real_model_attempted_batch_never_releases_candidates(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(55), stamp(clock()))
    batch = runtime.batches.create(
        Policy(initial_selection_version="v008", candidate_limit=30, max_approved=30)
    )
    runtime.batches.set_result(
        batch.id, "failed", WorkflowError("SELECTION_NOT_IMPLEMENTED", "未实现")
    )
    assert not release_unstarted_batches(runtime.factory)
