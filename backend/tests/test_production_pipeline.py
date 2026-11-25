import pytest
from conftest import Clock, sample_video
from production_support import WORKSPACE, FixtureGeneration, prepared_content
from test_pipeline import FakeChannel

from youmo_workflow.adapters.editing import ProductionEditor
from youmo_workflow.adapters.packaging import ProductionPackaging
from youmo_workflow.adapters.production_profile import ProductionProfile
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.providers import (
    PackagingRequest,
    SelectedContent,
    SelectionOutput,
    SelectionRequest,
)
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.time import stamp
from youmo_workflow.orchestration.runner import Providers, Runner
from youmo_workflow.runtime import Runtime


class PreparedSelector:
    def __init__(self, content: SelectedContent) -> None:
        self.content = content

    def select(self, request: SelectionRequest) -> SelectionOutput:
        return SelectionOutput(batch_id=request.batch_id, selected=[self.content], rejected_ids=[])


@pytest.mark.parametrize("video_seconds", [30, 29.8])
def test_real_packaging_render_edit_and_manifest_with_offline_model(
    runtime: Runtime, clock: Clock, video_seconds: float
) -> None:
    content = prepared_content(runtime, clock, video_seconds)
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    batch = runtime.batches.create(Policy())
    model, profile = FixtureGeneration(), ProductionProfile(WORKSPACE)
    providers = Providers(
        FakeChannel(),
        PreparedSelector(content),
        ProductionPackaging(runtime.artifacts, model, profile),
        runtime.publications,
    )
    providers.editor = ProductionEditor(runtime.artifacts, model, profile)
    runner = Runner(runtime.factory, providers, runtime.artifacts, clock)
    for _ in range(4):
        assert runner.once(), runtime.tasks.list_tasks()
    assert not runner.once()
    assert runtime.batches.detail(batch.id).status == "ready", runtime.tasks.list_tasks()
    published = runtime.publications.list_publications()[0]
    with runtime.factory() as db:
        row = runtime.publications.require(db, published.id)
        manifest = PublicationManifest.model_validate_json(row.manifest_json)
    assert manifest.media_ref != content.original_media_ref
    assert manifest.subtitle_ref != content.translation_ref
    assert manifest.visibility == "private" and manifest.test_only
    assert manifest.subtitle_mode == "hard" and manifest.burn_in_ref
    assert not manifest.rights_confirmed
    assert model.calls == [
        "title_raw_history_v001",
        "cover_plan_raw_history_v001",
        "cover_vision_raw_history_v001",
        "editing_v004_hardsub",
        "subtitle_llm_review_hardsub_v002",
    ]
    runtime.publications.confirm(published.id, published.manifest_hash)
    assert runtime.publications.plan(published.id).media_verified
    saved_calls = list(model.calls)
    cached = providers.packaging.package(PackagingRequest(content=content))
    assert model.calls == saved_calls
    assert cached.cover_ref == manifest.cover_ref and cached.title == manifest.title
    assert cached.packaging_version == "raw_history_poster_v001"
