"""Keyword masking, then model span review, then deterministic output verification."""

import re
from pathlib import Path
from typing import Literal

import pysubs2
from pydantic import Field, JsonValue

from youmo_workflow.adapters.production_files import encode, freeze, frozen_text
from youmo_workflow.adapters.subtitle_review import load_rules
from youmo_workflow.domain.content import GenerationProvider, GenerationRequest
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.generation import checked_generation
from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.providers import ReadyContent
from youmo_workflow.domain.subtitle_filter import WordFilter, mask_character


class MaskSpan(Contract):
    cue_id: str = Field(pattern=r"^C[0-9]{5}$")
    quote: str = Field(min_length=2, max_length=80)
    category: Literal["political", "sexual", "violence", "personal_data", "other"]
    reason: str = Field(min_length=1, max_length=200)


class ReviewDecision(Contract):
    safe_to_submit: bool
    mask_spans: list[MaskSpan] = Field(max_length=100)
    notes: list[str] = Field(max_length=20)


class ReviewContext(Contract):
    stage: Literal[
        "subtitle_llm_review_revise_v002",
        "subtitle_llm_review_hardsub_v002",
        "subtitle_llm_review_hardsub_v003",
    ] = "subtitle_llm_review_revise_v002"
    previous_platform_feedback: str = Field(default="", max_length=800)


def checked_decision(raw: str, cues: dict[str, str]) -> ReviewDecision:
    result = ReviewDecision.model_validate_json(raw)
    for span in result.mask_spans:
        text = cues.get(span.cue_id, "")
        if (
            "*" in span.quote
            or "\n" in span.quote
            or not any(span.quote in line for line in text.splitlines())
        ):
            raise WorkflowError(
                "SUBTITLE_REVIEW_SPAN_INVALID",
                "审查片段必须逐字存在于指定字幕的一行中，不能猜回星号或跨行。",
            )
    return result


def apply_decision(cues: dict[str, str], decision: ReviewDecision) -> list[str]:
    if not decision.safe_to_submit:
        raise WorkflowError(
            "SUBTITLE_LLM_REVIEW_BLOCKED", "模型复审未通过，保留原始资料，不强制模型改判。"
        )
    output, additional = list[str](), 0
    for cue_id, text in cues.items():
        marked = {
            index
            for span in decision.mask_spans
            if span.cue_id == cue_id
            for match in re.finditer(re.escape(span.quote), text)
            for index in range(match.start(), match.end())
        }
        additional += sum(not text[index].isspace() for index in marked)
        output.append(
            "".join(
                mask_character(char) if index in marked else char for index, char in enumerate(text)
            )
        )
    visible = sum(char != "*" and not char.isspace() for text in cues.values() for char in text)
    if additional > max(1, visible * 0.35):
        raise WorkflowError(
            "SUBTITLE_LLM_REVIEW_BLOCKED",
            "模型要求遮罩过多正文，停止提交，避免生成失去语义的字幕。",
        )
    return output


def subtitle_text(source: pysubs2.SSAFile, texts: list[str]) -> str:
    result = pysubs2.SSAFile()
    for cue, text in zip(source.events, texts, strict=True):
        event = pysubs2.SSAEvent(start=cue.start, end=cue.end)
        event.plaintext = text
        result.events.append(event)
    return result.to_string("srt")


class SubtitleAuditor:
    def __init__(
        self, model: GenerationProvider, config_root: Path, rules_path: Path | None = None
    ):
        self.model, self.config_root = model, config_root
        self.rules_path = rules_path or config_root / "subtitle-denylist.json"

    def review(
        self,
        request: ReadyContent,
        path: Path,
        directory: Path,
        *,
        context: ReviewContext | None = None,
    ) -> Path:
        source = pysubs2.load(str(path), encoding="utf-8")
        rules, digest = load_rules(self.rules_path)
        words = WordFilter(rules)
        filtered, counts = words.clean_cues([event.plaintext for event in source.events])
        cues = {f"C{index:05}": text for index, text in enumerate(filtered, 1)}
        freeze(directory / "input.srt", path.read_text(encoding="utf-8"))
        freeze(directory / "rules.json", rules.model_dump_json())
        freeze(directory / "keyword-masked.srt", subtitle_text(source, filtered))
        titles, _ = words.clean([request.packaging.title, request.content.original_title])
        payload: dict[str, JsonValue] = {
            "video_id": request.content.video_id,
            "title": titles[0],
            "original_title": titles[1],
            "rules_sha256": digest,
            "cues": [{"cue_id": key, "text": text} for key, text in cues.items()],
        }
        prompt = frozen_text(self.config_root / "subtitle-llm-review-v001/prompt.txt")
        if context and context.previous_platform_feedback:
            payload["previous_platform_feedback"] = context.previous_platform_feedback
        raw = checked_generation(
            self.model,
            GenerationRequest(
                content_id=request.content.content_id,
                stage=context.stage if context else "subtitle_llm_review_v001",
                prompt=prompt.replace("{{input_json}}", encode(payload)),
            ),
            lambda value: checked_decision(value, cues),
        )
        decision = checked_decision(raw, cues)
        freeze(directory / "decision.json", decision.model_dump_json())
        revised = apply_decision(cues, decision)
        _, remaining = words.clean_cues(revised)
        if remaining or any(
            len(left) != len(right) or left.count("\n") != right.count("\n")
            for left, right in zip(filtered, revised, strict=True)
        ):
            raise WorkflowError(
                "SUBTITLE_REVIEW_INVALID", "字幕复检失败；不允许引入禁词、删段或改字数。"
            )
        target = directory / "reviewed.zh-ja.srt"
        freeze(target, subtitle_text(source, revised))
        freeze(
            directory / "audit.json",
            encode(
                {
                    "rules_sha256": digest,
                    "keyword_matches": dict[str, JsonValue](counts),
                    "llm_spans": len(decision.mask_spans),
                    "cue_count": len(cues),
                    "timeline_changed": False,
                    "original_overwritten": False,
                    "platform_approval_guaranteed": False,
                }
            ),
        )
        return target
