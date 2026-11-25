from conftest import Clock
from selection_support import FixtureInitial, discovery_items
from sqlalchemy import select
from test_pipeline import FakeChannel

from youmo_workflow.adapters.blackboxes import UnimplementedPackaging, UnimplementedSelection
from youmo_workflow.adapters.selection_passthrough import (
    PassthroughRescreen,
    UnimplementedContentPreparation,
)
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.selection import InitialOutput
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.initial_selection import enqueue_initial, selection_results
from youmo_workflow.orchestration.runner import Providers, Runner
from youmo_workflow.orchestration.selection import SelectionProviders, SelectionStages
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task


def make_runner(runtime: Runtime, clock: Clock) -> Runner:
    providers = Providers(
        FakeChannel(), UnimplementedSelection(), UnimplementedPackaging(), runtime.publications
    )
    runner = Runner(runtime.factory, providers, runtime.artifacts, clock)
    runner.selection_stages = SelectionStages(
        runtime.factory,
        SelectionProviders(
            FixtureInitial(), PassthroughRescreen(), UnimplementedContentPreparation()
        ),
        clock,
    )
    return runner


def test_new_pipeline_saves_first_stage_and_passes_thirty_unchanged(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(), stamp(clock()))
    batch = runtime.batches.create(
        Policy(initial_selection_version="v004", candidate_limit=30, max_approved=30)
    )
    runner = make_runner(runtime, clock)
    assert runner.once() and runner.once() and runner.once()
    results = selection_results(runtime.factory, batch.id)
    assert results.initial is not None and results.rescreen == results.initial
    assert results.rescreen is not None
    assert len(results.rescreen.shortlisted_ids) == 30
    assert all(row.screened for row in runtime.sources.list_videos("", 0, 100).items)
    with runtime.factory() as db:
        rows = {row.kind: row for row in db.scalars(select(Task).where(Task.batch_id == batch.id))}
        assert rows["initial_selection"].output_json == rows["rescreen"].output_json
        assert rows["prepare_content"].error_code == "CONTENT_PREPARATION_NOT_IMPLEMENTED"
    assert not runtime.publications.list_publications()
    assert not runner.once()


def test_existing_batch_can_explicitly_start_v004_without_changing_old_snapshot(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(2), stamp(clock()))
    batch = runtime.batches.create(Policy())
    runner = make_runner(runtime, clock)
    runner.once()
    before = runtime.batches.detail(batch.id)
    first = enqueue_initial(runtime.factory, batch.id, clock, "v004")
    assert enqueue_initial(runtime.factory, batch.id, clock, "v004").id == first.id
    assert runtime.batches.detail(batch.id).items == before.items
    assert runtime.batches.detail(batch.id).policy == before.policy
    runner.once()
    assert selection_results(runtime.factory, batch.id).initial is not None
    with runtime.factory() as db:
        original = db.scalar(
            select(Task).where(Task.kind == "selection", Task.batch_id == batch.id)
        )
        assert original is not None and original.status == "failed"


class MutatingRescreen:
    def review(self, request: InitialOutput) -> InitialOutput:
        request.shortlisted_ids.pop()
        return request


def test_passthrough_guard_catches_in_place_mutation(runtime: Runtime, clock: Clock) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(2), stamp(clock()))
    batch = runtime.batches.create(
        Policy(initial_selection_version="v004", candidate_limit=30, max_approved=30)
    )
    runner = make_runner(runtime, clock)
    assert runner.selection_stages is not None
    runner.selection_stages.providers.rescreen = MutatingRescreen()
    runner.once()
    runner.once()
    assert runtime.batches.detail(batch.id).error_code == "RESCREEN_NOT_PASSTHROUGH"
    assert selection_results(runtime.factory, batch.id).rescreen is None
