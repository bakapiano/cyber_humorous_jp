import json
from typing import Literal

import pysubs2
import pytest
from batch_pipeline_support import RecordingGateway, batch_runner
from conftest import Assets, Clock
from PIL import Image
from production_support import WORKSPACE, FixtureGeneration, prepared_content
from pydantic import ValidationError
from selection_support import discovery_items
from sqlalchemy import select
from test_batch_trigger import configure, manual

from youmo_workflow.adapters.editing import ProductionEditor
from youmo_workflow.adapters.media import run_media
from youmo_workflow.adapters.packaging import ProductionPackaging
from youmo_workflow.adapters.production_profile import ProductionProfile
from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.editing import EditingOutput, RangePlan
from youmo_workflow.domain.editing_presentation import present_edit
from youmo_workflow.domain.providers import PackagingRequest, ReadyContent
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task


@pytest.mark.parametrize(
    "start,skip",
    [(0, True), (7, True), (9.999, True), (10, False), (10.001, False), (59, False), (902, False)],
)
def test_programmatic_first_ten_second_overlap(start: float, skip: bool) -> None:
    plan = RangePlan.model_validate(
        {"quick_recap": [{"start": start, "end": start + 4}], "main": {"start": 0, "end": 1000}}
    )
    original = plan.model_dump_json()
    result = present_edit(plan)
    assert result.recap_skipped is skip
    assert result.transition_seconds == (0 if skip else 0.6)
    assert len(result.timeline) == (1 if skip else 2)
    assert result.timeline[-1].output_start == (0 if skip else 4.6)
    assert result.timeline[-1].source_end == 1000
    assert plan.model_dump_json() == original


class IntroGeneration(FixtureGeneration):
    def __init__(self, early: bool) -> None:
        super().__init__()
        self.early = early

    def generate(self, request: GenerationRequest) -> str:
        if self.early and request.stage == "editing_v004_hardsub":
            self.calls.append(request.stage)
            return json.dumps(
                {"quick_recap": [{"start": 9.8, "end": 14}], "main": {"start": 0, "end": 30}}
            )
        return super().generate(request)


@pytest.mark.parametrize("early", [False, True])
def test_real_v005_render_remaps_subtitles_and_reuses_frozen_cache(
    runtime: Runtime, clock: Clock, early: bool
) -> None:
    content = prepared_content(runtime, clock)
    model, profile = IntroGeneration(early), ProductionProfile(WORKSPACE)
    packaging = ProductionPackaging(runtime.artifacts, model, profile).package(
        PackagingRequest(content=content)
    )
    old_request = ReadyContent(content=content, packaging=packaging)
    assert "editing_revision" not in old_request.model_dump()
    request = old_request.model_copy(update={"editing_revision": "v005"})
    editor = ProductionEditor(runtime.artifacts, model, profile)
    result = editor.edit(request)
    assert result.version == "v005_hardsub" and len(result.timeline) == (1 if early else 2)
    assert result.timeline[-1].output_end == (30 if early else 34.6)
    path = runtime.artifacts.resolve(result.subtitle_ref, "subtitle")
    cues = pysubs2.load(str(path)).events
    assert cues[0].start == 0
    assert any(cue.start == (0 if early else 4600) for cue in cues)
    if not early:
        assert all(cue.end <= 4000 or cue.start >= 4600 for cue in cues)
        frame = runtime.artifacts.root / "transition-center.png"
        run_media(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                "4.3",
                "-i",
                str(runtime.artifacts.resolve(result.media_ref, "media")),
                "-frames:v",
                "1",
                str(frame),
            ],
            frame.parent,
        )
        with Image.open(frame) as image:
            high = image.convert("L").getextrema()[1]
            assert isinstance(high, (int, float)) and high < 5
    assert model.calls[-2:] == ["editing_v004_hardsub", "subtitle_llm_review_hardsub_v003"]
    calls = list(model.calls)
    assert editor.edit(request) == result and model.calls == calls
    if early:
        with pytest.raises(ValidationError, match="Legacy"):
            EditingOutput.model_validate({**result.model_dump(), "version": "v004_hardsub"})


@pytest.mark.parametrize("revision", ["v004", "v005"])
def test_batch_revision_is_frozen_and_reaches_new_editing_tasks(
    runtime: Runtime, clock: Clock, assets: Assets, revision: Literal["v004", "v005"]
) -> None:
    setting = configure(runtime, clock, count=1)
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(setting))
    assert batch
    with runtime.factory.begin() as db:
        row = db.get(Batch, batch.id)
        assert row and row.execution_json
        execution = BatchExecution.model_validate_json(row.execution_json)
        assert execution.editing_revision == "v005"
        if revision == "v004":
            execution.editing_revision = "v004"
            legacy = execution.model_dump_json()
            row.execution_json = legacy
            assert "editing_revision" not in legacy
        frozen = row.execution_json
    runner = batch_runner(runtime, clock, assets, RecordingGateway())
    for _ in range(5):
        assert runner.once()
    with runtime.factory() as db:
        task = db.scalar(select(Task).where(Task.kind == "editing"))
        row = db.get(Batch, batch.id)
        assert task and row and row.execution_json == frozen
        assert ReadyContent.model_validate_json(task.input_json).editing_revision == revision
