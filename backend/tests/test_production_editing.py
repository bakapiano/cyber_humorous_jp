from pathlib import Path

import pysubs2
import pytest
from production_support import TITLE, citation, edit_plan, transcript

from youmo_workflow.adapters.editing_render import clip_inputs, make_filter, remap_subtitles
from youmo_workflow.adapters.editing_validation import make_timeline, validate_edit, validate_ranges
from youmo_workflow.domain.editing import RangePlan
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import ArtifactRef, PackagingOutput


def packaging() -> PackagingOutput:
    ref = ArtifactRef(artifact_id="synthetic", sha256="a" * 64)
    return PackagingOutput(
        content_id="synthetic",
        title=TITLE,
        cover_ref=ref,
        original_media_ref=ref,
        title_evidence=[citation()],
        editorial_angle="test",
        cover_timestamp=12,
    )


def test_title_aligned_plan_preserves_evidence_and_cover() -> None:
    plan = edit_plan()
    validate_edit(plan, transcript(), packaging())
    timeline = make_timeline(plan)
    assert timeline[0].output_end == timeline[1].output_start == 4
    assert timeline[-1].output_end == 34


@pytest.mark.parametrize(
    "change",
    [
        "title",
        "unsupported",
        "missing_evidence",
        "outside_cover",
        "truncated_quote",
        "invented_quote",
        "missing_context",
    ],
)
def test_editing_rejects_mismatched_title_or_evidence(change: str) -> None:
    plan, package = edit_plan(), packaging()
    if change == "title":
        plan.title_alignment.title = "Different title"
    elif change == "unsupported":
        plan.title_alignment.supported = False
    elif change == "missing_evidence":
        plan.title_alignment.evidence_segment_ids = []
    elif change == "outside_cover":
        package.cover_timestamp = 31
    elif change == "truncated_quote":
        plan.quick_recap[0].start = 11
    elif change == "invented_quote":
        plan.quick_recap[0].evidence[0].quote = "Not in source"
    else:
        plan.title_alignment.context_preserved = False
    with pytest.raises(WorkflowError):
        validate_edit(plan, transcript(), package)


def test_subtitles_repeat_recap_and_shift_full_main(tmp_path: Path) -> None:
    source, target = tmp_path / "source.srt", tmp_path / "edited.srt"
    source.write_text("1\n00:00:10,000 --> 00:00:14,000\n双语字幕\n日本語\n", encoding="utf-8")
    remap_subtitles(source, make_timeline(edit_plan()), target)
    rows = pysubs2.load(str(target)).events
    assert [(row.start, row.end) for row in rows] == [(0, 4000), (14000, 18000)]
    assert rows[0].plaintext == rows[1].plaintext == "双语字幕\n日本語"


def test_range_plan_uses_only_cached_times_not_recopied_model_quotes() -> None:
    original = edit_plan()
    original.main.evidence[0].quote = "Old model recopy typo, not a source of truth"
    raw = original.model_dump_json()
    ranges = RangePlan.model_validate_json(raw)
    validate_ranges(ranges, transcript(), packaging())
    assert ranges.main.start == original.main.start and ranges.main.end == original.main.end
    assert ranges.quick_recap[0].start == original.quick_recap[0].start
    assert "evidence" not in ranges.model_dump_json()
    assert original.model_dump_json() == raw


@pytest.mark.parametrize("change", ["main", "recap", "cover", "duration"])
def test_interval_only_plan_still_enforces_title_and_source_bounds(change: str) -> None:
    plan = RangePlan.model_validate_json(edit_plan().model_dump_json())
    package = packaging()
    if change == "main":
        plan.main.start = 11
    elif change == "recap":
        plan.quick_recap[0].start, plan.quick_recap[0].end = 20, 24
    elif change == "cover":
        package.cover_timestamp = 31
    else:
        plan.main.end = 31
    with pytest.raises(WorkflowError):
        validate_ranges(plan, transcript(), package)


def test_late_recap_seeks_independent_inputs_instead_of_buffering_main() -> None:
    plan = RangePlan.model_validate(
        {
            "quick_recap": [{"start": 1016.34, "end": 1023.34}],
            "main": {"start": 9.34, "end": 1048.56},
        }
    )
    timeline = make_timeline(plan)
    arguments = clip_inputs(Path("source.mp4"), timeline)
    assert arguments.count("-i") == 2
    assert arguments[arguments.index("-ss") + 1] == "1016.340000"
    graph = make_filter(timeline)
    assert "[0:v:0]trim=start=0:end=7.000000" in graph
    assert "[1:v:0]trim=start=0:end=1039.220000" in graph
    assert "1016.340000" not in graph
