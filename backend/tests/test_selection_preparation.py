from datetime import timedelta

import pytest
from conftest import Assets, Clock
from selection_support import discovery_items
from test_pipeline import FixtureEditing, FixturePackaging
from test_selection_stages import make_runner

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.providers import SelectedContent, SelectionOutput
from youmo_workflow.domain.selection import InitialOutput
from youmo_workflow.domain.time import parse_stamp, stamp
from youmo_workflow.features.initial_selection import enqueue_initial, selection_results
from youmo_workflow.orchestration.selection import validate_preparation
from youmo_workflow.runtime import Runtime


class FixtureContentPreparation:
    def __init__(self, assets: Assets, clock: Clock) -> None:
        self.assets, self.clock = assets, clock

    def prepare(self, request: InitialOutput) -> SelectionOutput:
        titles = {row.video.video_id: row.video.title for row in request.ranked}
        return SelectionOutput(
            batch_id=request.batch_id,
            rejected_ids=[],
            selected=[
                SelectedContent(
                    content_id=f"synthetic-{video_id}",
                    video_id=video_id,
                    original_title=titles[video_id],
                    original_media_ref=self.assets.media,
                    transcript_ref=self.assets.subtitle,
                    translation_ref=self.assets.subtitle,
                    evidence=["仅供隔离测试的自产素材"],
                    expires_at=stamp(self.clock() + timedelta(days=1)),
                )
                for video_id in request.shortlisted_ids
            ],
        )


def test_new_stages_can_reach_media_plan_with_explicit_test_only_providers(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(2), stamp(clock()))
    batch = runtime.batches.create(Policy())
    runner = make_runner(runtime, clock)
    runner.once()
    enqueue_initial(runtime.factory, batch.id, clock, "v004")
    assert runner.selection_stages is not None
    prepared = FixtureContentPreparation(assets, clock)
    runner.selection_stages.providers.content = prepared
    runner.providers.packaging = FixturePackaging(assets)
    runner.providers.editor = FixtureEditing(runtime, assets)
    for _ in range(9):
        assert runner.once()
    assert not runner.once()
    assert runtime.batches.detail(batch.id).status == "ready"
    assert len(runtime.publications.list_publications()) == 2
    assert all(row.test_only for row in runtime.publications.list_publications())
    source = selection_results(runtime.factory, batch.id).initial
    assert source is not None
    invalid = prepared.prepare(source)
    invalid.selected[1].content_id = invalid.selected[0].content_id
    with pytest.raises(WorkflowError):
        validate_preparation(source, invalid)


def test_stale_initial_worker_cannot_overwrite_a_newer_batch_state(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(2), stamp(clock()))
    batch = runtime.batches.create(
        Policy(initial_selection_version="v004", candidate_limit=30, max_approved=30)
    )
    task = runtime.tasks.claim()
    assert task is not None
    assert task.lease_until
    clock.at = parse_stamp(task.lease_until) + timedelta(seconds=1)
    runtime.batches.set_result(batch.id, "ready")
    make_runner(runtime, clock).fail(task, WorkflowError("LATE_ERROR", "stale"))
    assert runtime.batches.detail(batch.id).status == "ready"
