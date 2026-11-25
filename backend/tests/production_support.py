from datetime import timedelta
from pathlib import Path

from conftest import Clock
from pydantic import JsonValue

from youmo_workflow.adapters.media import run_media
from youmo_workflow.adapters.production_files import digest, encode
from youmo_workflow.domain.content import Citation, GenerationRequest, Segment, Transcript
from youmo_workflow.domain.editing import EditPlan
from youmo_workflow.domain.providers import SelectedContent
from youmo_workflow.domain.time import stamp
from youmo_workflow.runtime import Runtime

WORKSPACE = Path(__file__).resolve().parents[3]
TITLE = "日本这份安排真让人疑惑🤔：一家机构说明最新安排与具体执行方式"
TITLE_TEXTS = [TITLE, "😅原来日本的这份安排，还得看这些条件", "日本的新安排说清楚了吗？"]


def transcript(sha: str = "a" * 64) -> Transcript:
    return Transcript(
        video_id="abcdefghijk",
        source_sha256=sha,
        duration_seconds=30,
        segments=[
            Segment(segment_id="T001", start=0, end=4, text="これは開始の説明です。"),
            Segment(segment_id="T002", start=10, end=14, text="新しい仕組みを説明します。"),
            Segment(segment_id="T003", start=24, end=29, text="これが必要な条件です。"),
        ],
    )


def citation() -> Citation:
    return Citation(segment_id="T002", quote="新しい仕組みを説明します", claim="测试机构说明安排")


def edit_plan() -> EditPlan:
    return EditPlan.model_validate(
        {
            "topic": "测试主题",
            "source_timebase": "local_video_seconds",
            "quick_recap": [
                {
                    "start": 10,
                    "end": 14,
                    "reason": "测试完整短句",
                    "evidence": [citation().model_dump()],
                }
            ],
            "main": {
                "start": 0,
                "end": 30,
                "reason": "完整保留测试上下文",
                "scope": "full_report",
                "evidence": [citation().model_dump()],
            },
            "omitted_sections": [],
            "warnings": [],
            "checks": {
                "quick_recap_within_main": True,
                "quick_recap_total_3_to_8_seconds": True,
                "main_is_contiguous": True,
                "topic_context_preserved": True,
            },
            "title_alignment": {
                "title": TITLE,
                "supported": True,
                "evidence_segment_ids": ["T002"],
                "recap_support": "直接展示标题的测试依据",
                "context_preserved": True,
            },
        }
    )


def prepared_content(runtime: Runtime, clock: Clock, video_seconds: float = 30) -> SelectedContent:
    root = runtime.artifacts.root
    media = root / "synthetic-source.mp4"
    run_media(
        [
            "ffmpeg",
            "-v",
            "error",
            "-n",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=320x180:r=30:duration={video_seconds}",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(media),
        ],
        root,
    )
    source = transcript(digest(media))
    (root / "transcript.json").write_text(source.model_dump_json(), encoding="utf-8")
    (root / "translation.srt").write_text(
        "1\n00:00:00,000 --> 00:00:04,000\n开始说明\nこれは開始の説明です。\n\n"
        "2\n00:00:10,000 --> 00:00:14,000\n说明新安排\n新しい仕組みを説明します。\n\n"
        "3\n00:00:24,000 --> 00:00:29,000\n必要条件\nこれが必要な条件です。\n",
        encoding="utf-8",
    )
    return SelectedContent(
        content_id="synthetic-production",
        video_id=source.video_id,
        original_title="Synthetic Japanese source",
        expires_at=stamp(clock() + timedelta(days=1)),
        evidence=["Explicit synthetic test, not a real source"],
        original_media_ref=runtime.artifacts.register(media, "media", test_only=True),
        transcript_ref=runtime.artifacts.register(
            root / "transcript.json", "transcript", test_only=True
        ),
        translation_ref=runtime.artifacts.register(
            root / "translation.srt", "subtitle", test_only=True
        ),
    )


class FixtureGeneration:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, request: GenerationRequest) -> str:
        self.calls.append(request.stage)
        if request.stage in {
            "subtitle_llm_review_v001",
            "subtitle_llm_review_hardsub_v002",
            "subtitle_llm_review_hardsub_v003",
        }:
            return encode({"safe_to_submit": True, "mask_spans": [], "notes": []})
        if request.stage in {"editing_v003_json", "editing_v004_hardsub"}:
            assert "json" in request.prompt.lower()
            assert TITLE in request.prompt and "title_evidence" in request.prompt
            return encode(
                {"quick_recap": [{"start": 10, "end": 14}], "main": {"start": 0, "end": 30}}
            )
        if request.stage == "title_raw_history_v001":
            assert "【历史投稿标题原文】" in request.prompt
            return encode(raw_titles())
        if request.stage == "cover_plan_raw_history_v001":
            assert "publication_titles" in request.prompt
            assert all(text in request.prompt for text in TITLE_TEXTS)
            return encode(cover_plan())
        assert request.stage == "cover_vision_raw_history_v001" and len(request.images) == 4
        return encode(
            {
                "selected_frame_id": "F001",
                "visible_content": "人工生成的测试图形",
                "selection_reason": "仅测试",
                "subject_box": [0.05, 0.1, 0.4, 0.6],
                "recommended_title_id": "TITLE1",
                "warnings": [],
                "covers": [
                    {
                        "variant_id": variant,
                        "title_id": "TITLE1",
                        "phrase_id": phrase,
                        "lines": [text],
                        "anchor": "center_middle",
                        "red_keywords": ["测试"],
                    }
                    for variant, phrase, text in [("A", "P1", "测试封面"), ("B", "P2", "仅供测试")]
                ],
                "checks": {
                    "frame_matches_topic": True,
                    "phrase_supported_by_content": True,
                    "original_marks_preserved_in_plan": True,
                },
            }
        )


def raw_titles() -> dict[str, JsonValue]:
    return {
        "titles": [
            {"title_id": f"TITLE{i}", "text": text} for i, text in enumerate(TITLE_TEXTS, 1)
        ],
        "recommended_title_id": "TITLE1",
    }


def cover_plan() -> dict[str, JsonValue]:
    return {
        "titles": [
            {
                "title_id": f"TITLE{i}",
                "text": text,
                "evidence": [citation().model_dump(mode="json")],
            }
            for i, text in enumerate(TITLE_TEXTS, 1)
        ],
        "recommended_title_id": "TITLE1",
        "core_angle": "synthetic",
        "warnings": [],
        "cover_regions": [
            {"region_id": "R1", "start_sec": 8, "end_sec": 20, "evidence_segment_ids": ["T002"]}
        ],
        "cover_phrases": [
            {"phrase_id": name, "text": text, "kind": "comment", "evidence_segment_ids": ["T002"]}
            for name, text in [("P1", "测试封面"), ("P2", "仅供测试")]
        ],
    }
