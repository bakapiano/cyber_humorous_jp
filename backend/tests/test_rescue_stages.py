from datetime import timedelta
from pathlib import Path

import pytest
from conftest import Clock
from rescue_support import FixtureRescueProfile, rescue_completion
from selection_support import completion, discovery_items
from sqlalchemy import select
from test_selection_stages import make_runner

from youmo_workflow.adapters.rescue_composition import compose_output, rescue_call
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.selection import InitialOutput, InitialRequest
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.initial_selection import enqueue_initial, selection_results
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task


class FixtureV008:
    def rank(self, request: InitialRequest) -> InitialOutput:
        call = FixtureRescueProfile(Path("synthetic")).freeze(request)
        baseline = completion(call)
        rescue = rescue_call(call, baseline)
        return compose_output(call, baseline, rescue_completion(call), rescue.prompt_sha256)


def test_upgrade_keeps_v004_results_and_does_not_mix_rescreen_generations(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(60), stamp(clock()))
    batch = runtime.batches.create(
        Policy(initial_selection_version="v004", candidate_limit=30, max_approved=30)
    )
    runner = make_runner(runtime, clock)
    assert runner.selection_stages is not None
    runner.selection_stages.providers.v008 = FixtureV008()
    assert runner.once() and runner.once() and runner.once()
    before = selection_results(runtime.factory, batch.id)
    assert before.initial is not None and before.initial.version == "v004"
    first = enqueue_initial(runtime.factory, batch.id, clock)
    assert enqueue_initial(runtime.factory, batch.id, clock).id == first.id
    pending = selection_results(runtime.factory, batch.id)
    assert pending.initial is None and pending.rescreen is None
    assert pending.history == [before.initial]
    assert runner.once() and runner.once() and runner.once()
    after = selection_results(runtime.factory, batch.id)
    assert after.initial is not None and after.initial.version == "v008"
    assert after.rescreen == after.initial
    assert after.history == [before.initial]
    with runtime.factory() as db:
        old_failure = db.scalar(
            select(Task).where(Task.unique_key == f"prepare_content:v004:{batch.id}")
        )
        assert old_failure is not None and old_failure.status == "failed"
        old_id = old_failure.id
    with pytest.raises(WorkflowError, match="旧选题阶段"):
        runtime.tasks.retry(old_id)


def test_new_policy_enqueues_v008_and_uses_a_longer_two_call_lease(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(60), stamp(clock()))
    runtime.batches.create(
        Policy(initial_selection_version="v008", candidate_limit=30, max_approved=30)
    )
    task = runtime.tasks.claim()
    assert task is not None and task.unique_key.startswith("initial:v008:")
    clock.at += timedelta(minutes=31)
    assert runtime.tasks.finish(task)


def test_old_failed_stage_cannot_retry_after_v008_is_registered(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(60), stamp(clock()))
    batch = runtime.batches.create(Policy())
    runner = make_runner(runtime, clock)
    runner.once()
    old = runtime.tasks.list_tasks()[0]
    enqueue_initial(runtime.factory, batch.id, clock)
    with pytest.raises(WorkflowError, match="旧选题阶段"):
        runtime.tasks.retry(old.id)
