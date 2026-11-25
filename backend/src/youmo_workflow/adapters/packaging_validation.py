import re

from youmo_workflow.adapters.cover_poster import check_poster_variant
from youmo_workflow.adapters.packaging_models import CoverChoice, CoverPlanning, Frame
from youmo_workflow.domain.content import Citation, Transcript
from youmo_workflow.domain.errors import WorkflowError


def require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkflowError("PRODUCTION_VALIDATION_FAILED", message)


def normalize_quote(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("｢", "「").replace("｣", "」")


def check_citations(citations: list[Citation], transcript: Transcript) -> None:
    segments = {row.segment_id: row for row in transcript.segments}
    for citation in citations:
        segment = segments.get(citation.segment_id)
        require(segment is not None, "引用了不存在的字幕证据。")
        assert segment is not None
        require(
            bool(normalize_quote(citation.quote))
            and normalize_quote(citation.quote) in normalize_quote(segment.text),
            "模型引文与原始字幕不匹配。",
        )


def check_planning(plan: CoverPlanning, transcript: Transcript, *, poster: bool = False) -> None:
    ids = {row.title_id for row in plan.titles}
    require(
        ids == {"TITLE1", "TITLE2", "TITLE3"} and plan.recommended_title_id in ids,
        "封面规划的标题身份无效。",
    )
    for title in plan.titles:
        check_citations(title.evidence, transcript)
    source_ids = {row.segment_id for row in transcript.segments}
    for region in plan.cover_regions:
        require(
            0 <= region.start_sec < region.end_sec <= transcript.duration_seconds,
            "封面候选区间越界。",
        )
        require(7.9 <= region.end_sec - region.start_sec <= 30.1, "封面区间长度无效。")
        require(set(region.evidence_segment_ids) <= source_ids, "封面区间证据不存在。")
    for phrase in plan.cover_phrases:
        require(len(phrase.text) <= (24 if poster else 8), "封面短句超出当前版本长度。")
        require(set(phrase.evidence_segment_ids) <= source_ids, "封面文字证据不存在。")
    require(
        len({row.region_id for row in plan.cover_regions}) == len(plan.cover_regions),
        "封面区间ID重复。",
    )
    require(
        len({row.phrase_id for row in plan.cover_phrases}) == len(plan.cover_phrases),
        "封面短句ID重复。",
    )


def check_choice(
    choice: CoverChoice, plan: CoverPlanning, frames: list[Frame], *, poster: bool = False
) -> None:
    require(choice.selected_frame_id in {row.frame_id for row in frames}, "选帧不在实际图像中。")
    require(choice.recommended_title_id == plan.recommended_title_id, "看图阶段修改了推荐标题。")
    require({row.variant_id for row in choice.covers} == {"A", "B"}, "必须保留A/B封面。")
    box = choice.subject_box
    require(
        all(0 <= value <= 1 for value in box) and box[0] < box[2] and box[1] < box[3],
        f"主体位置无效：subject_box={box}；必须为0–1范围的[x1,y1,x2,y2]，且x1<x2、y1<y2。",
    )
    phrases = {row.phrase_id: row.text for row in plan.cover_phrases}
    for cover in choice.covers:
        require(cover.title_id == plan.recommended_title_id, "封面没有配合推荐标题。")
        require("".join(cover.lines) == phrases.get(cover.phrase_id), "封面修改了原始短句。")
        if poster:
            check_poster_variant(cover)
        else:
            require(
                len(cover.lines) <= 2
                and all(1 <= len(line) <= 4 for line in cover.lines)
                and not cover.red_keywords
                and cover.anchor != "center_middle",
                "旧版封面仅允许1–2行、每行最多四字的纯黄字左右布局。",
            )
    require(
        all(
            choice.checks.get(key) is True
            for key in (
                "frame_matches_topic",
                "phrase_supported_by_content",
                "original_marks_preserved_in_plan",
            )
        ),
        "封面看图自检未通过。",
    )
