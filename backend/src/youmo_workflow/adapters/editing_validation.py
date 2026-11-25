from typing import Literal

from youmo_workflow.adapters.packaging_validation import check_citations, require
from youmo_workflow.domain.content import Transcript
from youmo_workflow.domain.editing import Clip, EditPlan, RangePlan, TimelinePart
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import PackagingOutput


def require_recap_feasible(
    transcript: Transcript, packaging: PackagingOutput, limit: Literal[8, 60] = 8
) -> None:
    check_citations(packaging.title_evidence, transcript)
    ids = {row.segment_id for row in packaging.title_evidence}
    require(bool(ids), "最终标题缺少字幕依据。")
    spans = [row.end - row.start for row in transcript.segments if row.segment_id in ids]
    # validate_ranges permits at most 0.25 seconds of trimming on either edge.
    # A longer evidence segment cannot fit any permitted 3–8 second recap.
    impossible = min(spans) - 0.5 >= 60 if limit == 60 else min(spans) - 0.5 > 8.001
    if impossible:
        raise WorkflowError(
            "EDITING_RECAP_UNSATISFIABLE",
            f"最短标题依据为{min(spans):.2f}秒，不能完整放入当前{limit}秒回看上限；未调用模型。",
        )


def validate_edit(plan: EditPlan, transcript: Transcript, packaging: PackagingOutput) -> None:
    main = plan.main
    alignment = plan.title_alignment
    require(alignment.supported and alignment.context_preserved, "素材不能支撑标题或上下文不完整。")
    require(alignment.title == packaging.title, "剪辑阶段改写了最终标题。")
    require(bool(alignment.recap_support.strip()), "缺少快速回看与标题的对应说明。")
    require(main.end - main.start >= 20, "正文不足20秒。")
    if main.scope == "full_report":
        require(
            main.start <= 2 and main.end >= transcript.duration_seconds - 3,
            "完整报道未保留首尾上下文。",
        )
    for clip in [main, *plan.quick_recap]:
        validate_clip(clip, transcript, recap=clip is not main)
    quick_seconds = sum(clip.end - clip.start for clip in plan.quick_recap)
    require(3 <= quick_seconds <= 8.001, "快速回看必须为3—8秒。")
    previous = main.start
    for clip in plan.quick_recap:
        require(
            main.start <= previous <= clip.start < clip.end <= main.end, "快速回看范围或顺序无效。"
        )
        require(clip.end - clip.start >= 1.5, "快速回看单段不足1.5秒。")
        previous = clip.end
    for omitted in plan.omitted_sections:
        require(0 <= omitted.start < omitted.end <= transcript.duration_seconds, "舍弃区间越界。")
        require(omitted.end <= main.start or omitted.start >= main.end, "舍弃区间与正文重叠。")
    require(
        all(
            plan.checks.get(key) is True
            for key in (
                "quick_recap_within_main",
                "quick_recap_total_3_to_8_seconds",
                "main_is_contiguous",
                "topic_context_preserved",
            )
        ),
        "剪辑方案自检未通过。",
    )
    validate_title_coverage(plan, transcript, packaging)


def validate_clip(clip: Clip, transcript: Transcript, *, recap: bool) -> None:
    require(0 <= clip.start < clip.end <= transcript.duration_seconds, "剪辑区间越界。")
    check_citations(clip.evidence, transcript)
    segments = {row.segment_id: row for row in transcript.segments}
    for citation in clip.evidence:
        row = segments[citation.segment_id]
        require(min(row.end, clip.end) > max(row.start, clip.start), "片段引用不在选取范围内。")
        if recap:
            require(
                clip.start <= row.start + 0.25 and clip.end >= row.end - 0.25,
                "快速回看截断了被引用的完整语句。",
            )


def validate_title_coverage(
    plan: EditPlan, transcript: Transcript, packaging: PackagingOutput
) -> None:
    check_citations(packaging.title_evidence, transcript)
    required = {row.segment_id for row in packaging.title_evidence}
    require(bool(required), "标题缺少可核对的字幕证据。")
    require(set(plan.title_alignment.evidence_segment_ids) == required, "剪辑未保留完整标题依据。")
    segments = {row.segment_id: row for row in transcript.segments}
    for sid in required:
        row = segments[sid]
        require(
            plan.main.start <= row.start and plan.main.end >= row.end,
            "正文删掉了标题所依据的完整字幕。",
        )
    recap_ids = {row.segment_id for clip in plan.quick_recap for row in clip.evidence}
    require(bool(required & recap_ids), "快速回看没有直接呈现标题证据。")
    require(packaging.cover_timestamp is not None, "封面缺少原片时间点。")
    assert packaging.cover_timestamp is not None
    require(
        plan.main.start <= packaging.cover_timestamp < plan.main.end, "封面画面未包含在正文中。"
    )


def validate_ranges(
    plan: RangePlan, transcript: Transcript, packaging: PackagingOutput, limit: Literal[8, 60] = 8
) -> None:
    main, recap = plan.main, plan.quick_recap[0]
    require(0 <= main.start < main.end <= transcript.duration_seconds, "正文区间越界。")
    require(main.end - main.start >= 20, "正文不足20秒。")
    require(main.start <= recap.start < recap.end <= main.end, "快速回看不在正文内。")
    length = recap.end - recap.start
    require(
        3 <= length < 60 if limit == 60 else 3 <= length <= 8.001,
        "快速回看必须至少3秒且严格小于60秒。" if limit == 60 else "快速回看必须为3—8秒。",
    )
    check_citations(packaging.title_evidence, transcript)
    ids = {row.segment_id for row in packaging.title_evidence}
    require(bool(ids), "最终标题缺少字幕依据。")
    evidence = [row for row in transcript.segments if row.segment_id in ids]
    require(
        all(main.start <= row.start and row.end <= main.end for row in evidence),
        "正文没有完整保留全部标题依据。",
    )
    require(
        any(recap.start <= row.start + 0.25 and recap.end >= row.end - 0.25 for row in evidence),
        "快速回看没有完整呈现标题依据。",
    )
    require(packaging.cover_timestamp is not None, "封面缺少源时间点。")
    assert packaging.cover_timestamp is not None
    require(main.start <= packaging.cover_timestamp < main.end, "封面画面不在正文内。")


def make_timeline(plan: EditPlan | RangePlan) -> list[TimelinePart]:
    cursor, parts = 0.0, list[TimelinePart]()
    for index, clip in enumerate([*plan.quick_recap, plan.main]):
        length = clip.end - clip.start
        parts.append(
            TimelinePart(
                part="main" if index == len(plan.quick_recap) else "quick_recap",
                source_start=clip.start,
                source_end=clip.end,
                output_start=round(cursor, 6),
                output_end=round(cursor + length, 6),
            )
        )
        cursor += length
    return parts
