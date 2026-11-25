import json
from pathlib import Path

import pysubs2
import pytest
from conftest import Assets, Clock
from production_support import WORKSPACE

from youmo_workflow.adapters.subtitle_llm_review import (
    ReviewDecision,
    SubtitleAuditor,
    apply_decision,
    checked_decision,
)
from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import PackagingOutput, ReadyContent, SelectedContent
from youmo_workflow.domain.time import stamp


class ReviewModel:
    def __init__(self, approved: bool = True):
        self.requests: list[GenerationRequest] = []
        self.approved = approved

    def generate(self, request: GenerationRequest) -> str:
        self.requests.append(request)
        return json.dumps(
            {
                "safe_to_submit": self.approved,
                "mask_spans": [
                    {"cue_id": "C00001", "quote": "甲乙", "category": "other", "reason": "合成测试"}
                ],
                "notes": [],
            }
        )


def ready(assets: Assets, clock: Clock) -> ReadyContent:
    content = SelectedContent(
        content_id="synthetic-review",
        video_id="abcdefghijk",
        original_title="普通素材",
        original_media_ref=assets.media,
        transcript_ref=assets.media,
        translation_ref=assets.subtitle,
        evidence=[],
        expires_at=stamp(clock()),
    )
    return ReadyContent(
        content=content,
        packaging=PackagingOutput(
            content_id=content.content_id,
            title="普通测试标题",
            cover_ref=assets.cover,
            original_media_ref=assets.media,
        ),
    )


def test_keywords_then_model_then_verified_derived_file(
    tmp_path: Path, assets: Assets, clock: Clock
) -> None:
    source = tmp_path / "input.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:01,900\n普通报道性关系甲乙后续信息\n普通の報道です\n",
        encoding="utf-8",
    )
    original = source.read_bytes()
    model = ReviewModel()
    result = SubtitleAuditor(model, WORKSPACE / "workflow/config").review(
        ready(assets, clock), source, tmp_path / "audit"
    )
    cues = pysubs2.load(str(result), encoding="utf-8")
    assert cues[0].plaintext == "普通报道*****后续信息\n普通の報道です"
    assert cues[0].start == 0 and cues[0].end == 1900
    assert source.read_bytes() == original
    assert len(model.requests) == 1 and model.requests[0].stage == "subtitle_llm_review_v001"
    assert "普通报道***甲乙后续信息" in model.requests[0].prompt
    assert (result.parent / "decision.json").exists() and (result.parent / "rules.json").exists()


@pytest.mark.parametrize("quote", ["不存在", "**", "甲乙\n丙丁"])
def test_review_cannot_invent_or_unmask_quotes(quote: str) -> None:
    raw = json.dumps(
        {
            "safe_to_submit": True,
            "mask_spans": [
                {"cue_id": "C00001", "quote": quote, "category": "other", "reason": "合成"}
            ],
            "notes": [],
        }
    )
    with pytest.raises(WorkflowError):
        checked_decision(raw, {"C00001": "甲乙丙丁***"})


def test_review_rejection_is_not_a_format_error_or_forced_approval(
    tmp_path: Path, assets: Assets, clock: Clock
) -> None:
    path = tmp_path / "source.srt"
    path.write_text("1\n00:00:00,000 --> 00:00:01,000\n普通文字甲乙后续\n", encoding="utf-8")
    model = ReviewModel(False)
    with pytest.raises(WorkflowError, match="未通过"):
        SubtitleAuditor(model, WORKSPACE / "workflow/config").review(
            ready(assets, clock), path, tmp_path / "audit"
        )
    assert len(model.requests) == 1 and not (tmp_path / "audit/reviewed.zh-ja.srt").exists()


def test_excessive_model_masking_is_rejected() -> None:
    raw = json.dumps(
        {
            "safe_to_submit": True,
            "mask_spans": [
                {"cue_id": "C00001", "quote": "甲乙丙丁", "category": "other", "reason": "合成"}
            ],
            "notes": [],
        }
    )
    decision = ReviewDecision.model_validate_json(raw)
    with pytest.raises(WorkflowError, match="过多正文"):
        apply_decision({"C00001": "甲乙丙丁末尾"}, decision)
