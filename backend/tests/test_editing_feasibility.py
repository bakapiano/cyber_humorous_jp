from pathlib import Path

import pytest
from conftest import Clock
from production_support import (
    TITLE,
    WORKSPACE,
    FixtureGeneration,
    citation,
    prepared_content,
    transcript,
)

from youmo_workflow.adapters.editing import ProductionEditor
from youmo_workflow.adapters.editing_validation import require_recap_feasible, validate_ranges
from youmo_workflow.adapters.production_profile import ProductionProfile
from youmo_workflow.domain.editing import RangePlan
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import ArtifactRef, PackagingOutput, ReadyContent
from youmo_workflow.runtime import Runtime


def package() -> PackagingOutput:
    ref = ArtifactRef(artifact_id="synthetic", sha256="a" * 64)
    return PackagingOutput(
        content_id="synthetic-production",
        title=TITLE,
        cover_ref=ref,
        original_media_ref=ref,
        title_evidence=[citation()],
        cover_timestamp=12,
    )


@pytest.mark.parametrize("length", [4, 8, 8.5])
def test_feasible_recap_preserves_existing_edge_tolerance(length: float) -> None:
    source = transcript()
    source.segments[1].end = 10 + length
    require_recap_feasible(source, package())
    plan = RangePlan.model_validate(
        {
            "quick_recap": [{"start": 10.25, "end": 10 + length - 0.25}],
            "main": {"start": 0, "end": 30},
        }
    )
    validate_ranges(plan, source, package())


def test_impossible_recap_stops_before_model_and_preserves_input(
    runtime: Runtime, clock: Clock, tmp_path: Path
) -> None:
    source = transcript()
    source.duration_seconds = 100
    source.segments = [source.segments[1].model_copy(update={"end": 80})]
    original = source.model_dump_json()
    model = FixtureGeneration()
    editor = ProductionEditor(runtime.artifacts, model, ProductionProfile(WORKSPACE))
    request = ReadyContent(content=prepared_content(runtime, clock), packaging=package())
    with pytest.raises(WorkflowError) as error:
        editor.plan(request, {}, tmp_path / "editing_v003", source)
    assert error.value.code == "EDITING_RECAP_UNSATISFIABLE"
    assert not model.calls and source.model_dump_json() == original


def test_short_unrelated_segment_does_not_make_long_title_evidence_feasible() -> None:
    source = transcript()
    source.segments[1].end = 22
    with pytest.raises(WorkflowError, match="最短标题依据"):
        require_recap_feasible(source, package())


@pytest.mark.parametrize(
    "length,accepted", [(12.02, True), (59.999, True), (60, False), (60.01, False)]
)
def test_new_recap_limit_is_strictly_less_than_one_minute(length: float, accepted: bool) -> None:
    source = transcript()
    source.duration_seconds = 100
    source.segments[1].end = 22
    value = RangePlan.model_validate(
        {"quick_recap": [{"start": 10, "end": 10 + length}], "main": {"start": 0, "end": 100}}
    )
    require_recap_feasible(source, package(), 60)
    if accepted:
        validate_ranges(value, source, package(), 60)
    else:
        with pytest.raises(WorkflowError, match="严格小于60"):
            validate_ranges(value, source, package(), 60)
