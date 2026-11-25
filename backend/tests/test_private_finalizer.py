from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from batch_pipeline_support import offline_live_assets
from conftest import Assets, Clock
from test_publications import manifest
from test_remote import FakeGateway

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.remote import RemoteSubmission
from youmo_workflow.orchestration.finalization import PrivateFinalizer
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task


class DelayedGateway(FakeGateway):
    def attach_and_verify(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        self.writes += 1
        return False


def uploaded(runtime: Runtime, clock: Clock, assets: Assets, gateway: FakeGateway) -> Task:
    value = manifest(offline_live_assets(runtime, assets), clock)
    value.test_only, value.rights_confirmed = False, True
    row = runtime.publications.prepare(value)
    runtime.publications.confirm(row.id, row.manifest_hash)
    scoped = replace(runtime, settings=replace(runtime.settings, publication_enabled=True))
    PublicationExecutor(scoped, gateway).submit_private(row.id)
    task = runtime.tasks.claim(("finalize_private",))
    assert task
    return task


def test_delayed_subtitle_visibility_retries_only_read_queries(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    gateway = DelayedGateway()
    task = uploaded(runtime, clock, assets, gateway)
    finalizer = PrivateFinalizer(runtime, gateway, legacy_native_subtitles=True)
    with pytest.raises(WorkflowError) as pending:
        finalizer.run(task)
    assert pending.value.retryable and gateway.writes == 2
    runtime.tasks.fail(task, pending.value)
    clock.at += timedelta(seconds=61)
    resumed = runtime.tasks.claim(("finalize_private",))
    assert resumed
    gateway.has_subtitle = True
    assert '"completed"' in finalizer.run(resumed)
    assert gateway.writes == 2 and not runtime.settings.publication_enabled


def test_finalizer_requires_matching_hash_and_live_lease(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    gateway = FakeGateway()
    task = uploaded(runtime, clock, assets, gateway)
    request = FinalizePrivate.model_validate_json(task.input_json)
    task.input_json = request.model_copy(update={"manifest_hash": "changed"}).model_dump_json()
    with pytest.raises(WorkflowError, match="不匹配"):
        PrivateFinalizer(runtime, gateway).run(task)
    clock.at += timedelta(minutes=16)
    with pytest.raises(WorkflowError, match="租约失效"):
        PrivateFinalizer(runtime, gateway).run(task)
    assert gateway.writes == 1


def test_rejected_subtitle_does_not_become_a_fresh_write_on_empty_read(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    gateway = FakeGateway()
    task = uploaded(runtime, clock, assets, gateway)
    request = FinalizePrivate.model_validate_json(task.input_json)
    with runtime.factory.begin() as db:
        runtime.publications.require(db, request.publication_id).status = "subtitle_rejected"
    with pytest.raises(WorkflowError) as error:
        PrivateFinalizer(runtime, gateway).run(task)
    assert error.value.code == "SUBTITLE_REVISION_REQUIRED" and not error.value.retryable
    assert gateway.writes == 1


@pytest.mark.parametrize("terminal", ["subtitle_rejected", "withdrawn"])
def test_stale_wait_observation_cannot_regress_terminal_state(
    runtime: Runtime, clock: Clock, assets: Assets, terminal: str
) -> None:
    gateway = FakeGateway()
    task = uploaded(runtime, clock, assets, gateway)
    request = FinalizePrivate.model_validate_json(task.input_json)
    with runtime.factory.begin() as db:
        runtime.publications.require(db, request.publication_id).status = terminal
    assert (
        PublicationExecutor(runtime, gateway).set_status(
            request.publication_id, "awaiting_subtitles"
        )
        == terminal
    )
