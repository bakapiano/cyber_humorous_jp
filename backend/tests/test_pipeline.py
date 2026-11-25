from datetime import timedelta

import pytest
from conftest import Assets, Clock, sample_video

from youmo_workflow.adapters.blackboxes import UnimplementedPackaging, UnimplementedSelection
from youmo_workflow.domain.editing import EditingOutput, TimelinePart
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import DiscoveryVideo, Policy
from youmo_workflow.domain.providers import (
    PackagingOutput,
    PackagingRequest,
    ReadyContent,
    SelectedContent,
    SelectionOutput,
    SelectionRequest,
)
from youmo_workflow.domain.time import stamp
from youmo_workflow.orchestration.runner import Providers, Runner, validate_selection
from youmo_workflow.runtime import Runtime


class FakeChannel:
    def collect(self, handle: str, since: str) -> list[DiscoveryVideo]:
        return []

    def lookup(self, video_ids: list[str]) -> list[DiscoveryVideo]:
        return []


class FixtureSelection:
    def __init__(self, assets: Assets, clock: Clock) -> None:
        self.assets, self.clock = assets, clock

    def select(self, request: SelectionRequest) -> SelectionOutput:
        first = request.items[0]
        content = SelectedContent(
            content_id="explicit-test-content",
            video_id=first.video_id,
            original_title=first.title,
            original_media_ref=self.assets.media,
            transcript_ref=self.assets.subtitle,
            translation_ref=self.assets.subtitle,
            evidence=["自产测试夹具，不是真实筛选结论"],
            expires_at=stamp(self.clock() + timedelta(days=1)),
        )
        return SelectionOutput(
            batch_id=request.batch_id,
            selected=[content],
            rejected_ids=[v.video_id for v in request.items[1:]],
        )


class FixturePackaging:
    def __init__(self, assets: Assets) -> None:
        self.assets = assets

    def package(self, request: PackagingRequest) -> PackagingOutput:
        return PackagingOutput(
            content_id=request.content.content_id,
            title="仅供离线联调的自产测试片",
            cover_ref=self.assets.cover,
            original_media_ref=self.assets.media,
        )


class FixtureEditing:
    def __init__(self, runtime: Runtime, assets: Assets) -> None:
        self.runtime, self.assets = runtime, assets

    def edit(self, request: ReadyContent) -> EditingOutput:
        path = self.runtime.artifacts.root / "fixture-plan.json"
        path.write_text('{"fixture":true}', encoding="utf-8")
        return EditingOutput(
            content_id=request.content.content_id,
            title=request.packaging.title,
            original_media_ref=self.assets.media,
            media_ref=self.assets.media,
            subtitle_ref=self.assets.subtitle,
            plan_ref=self.runtime.artifacts.register(path, "edit_plan", test_only=True),
            timeline=[
                TimelinePart(
                    part="quick_recap", source_start=0, source_end=1, output_start=0, output_end=1
                ),
                TimelinePart(
                    part="main", source_start=0, source_end=1, output_start=1, output_end=2
                ),
            ],
        )


def test_real_mode_stops_at_selection(runtime: Runtime, clock: Clock) -> None:
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    batch = runtime.batches.create(Policy())
    providers = Providers(
        FakeChannel(), UnimplementedSelection(), UnimplementedPackaging(), runtime.publications
    )
    runner = Runner(runtime.factory, providers, runtime.artifacts, clock)
    assert runner.once()
    assert runtime.tasks.list_tasks()[0].error_code == "SELECTION_NOT_IMPLEMENTED"
    assert runtime.batches.detail(batch.id).status == "failed"
    assert not runtime.publications.list_publications()
    assert not runner.once()


def test_fixture_pipeline_reaches_verified_plan(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    batch = runtime.batches.create(Policy())
    providers = Providers(
        FakeChannel(),
        FixtureSelection(assets, clock),
        FixturePackaging(assets),
        runtime.publications,
    )
    runner = Runner(runtime.factory, providers, runtime.artifacts, clock)
    providers.editor = FixtureEditing(runtime, assets)
    assert runner.once()
    assert runner.once()
    assert runner.once()
    assert runner.once()
    assert not runner.once()
    assert runtime.batches.detail(batch.id).status == "ready"
    publications = runtime.publications.list_publications()
    assert len(publications) == 1 and publications[0].test_only
    prepared = publications[0]
    runtime.publications.confirm(prepared.id, prepared.manifest_hash)
    plan = runtime.publications.plan(prepared.id)
    assert plan.execution == "dry_run" and not plan.live_enabled
    assert plan.media_verified and plan.subtitle_verified


def test_packaging_is_independent_blackbox(runtime: Runtime, clock: Clock, assets: Assets) -> None:
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    runtime.batches.create(Policy())
    providers = Providers(
        FakeChannel(),
        FixtureSelection(assets, clock),
        UnimplementedPackaging(),
        runtime.publications,
    )
    runner = Runner(runtime.factory, providers, runtime.artifacts, clock)
    runner.once()
    runner.once()
    assert any(t.error_code == "PACKAGING_NOT_IMPLEMENTED" for t in runtime.tasks.list_tasks())
    assert not runtime.publications.list_publications()


def test_selector_cannot_invent_ids(runtime: Runtime, clock: Clock) -> None:
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    request = SelectionRequest(
        batch_id="batch",
        items=runtime.sources.list_videos("", 0, 10).items,
        candidate_limit=6,
        max_approved=3,
    )
    result = SelectionOutput(batch_id="batch", selected=[], rejected_ids=["unknown-id"])
    with pytest.raises(WorkflowError):
        validate_selection(request, result)
