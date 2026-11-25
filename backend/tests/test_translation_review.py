from pathlib import Path

import httpx
import pysubs2
import pytest
from production_support import transcript
from pydantic import TypeAdapter

from youmo_workflow.adapters.production_model import ProductionModel
from youmo_workflow.adapters.translation import (
    TranslatedSegment,
    Translation,
    translate,
    translation_request,
)
from youmo_workflow.adapters.translation_review import TranslationReview
from youmo_workflow.domain.content import GenerationRequest, Segment
from youmo_workflow.domain.errors import WorkflowError

POLICY = Path(__file__).parents[2] / "config/subtitle-denylist.json"


class TranslationModel:
    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> str:
        self.requests.append(request)
        rows = TypeAdapter(list[Segment]).validate_json(request.prompt.split("资料：\n")[-1])
        return Translation(
            segments=[
                TranslatedSegment(
                    segment_id=row.segment_id, translation=row.text.replace("説明", "说明习近平")
                )
                for row in rows
            ],
            warnings=[],
        ).model_dump_json()


def test_new_translation_receives_filtered_text_and_keeps_raw_response(tmp_path: Path) -> None:
    source = transcript()
    source.segments[0].text = "習近平説明"
    source.segments[1].text = "法輪功"
    before = source.model_dump_json()
    model = TranslationModel()
    review = TranslationReview(POLICY, lambda _: None)
    path = translate(model, "test", source, tmp_path, review)
    request = model.requests[0]
    assert request.stage == "translation_v003_masked"
    assert "習近平" not in request.prompt and "法輪功" not in request.prompt
    assert "T002" in request.prompt and "***説明" in request.prompt
    assert "不要推测" in request.prompt
    cues = pysubs2.load(str(path))
    assert len(cues) == 3 and cues[0].plaintext == "***说明***\n***説明"
    assert cues[1].plaintext == "***\n***"
    assert "习近平" in (tmp_path / "translation.json").read_text(encoding="utf-8")
    assert source.model_dump_json() == before
    assert (tmp_path / "translation-review-v003/rules.json").is_file()


def test_all_masked_preserves_all_segments(tmp_path: Path) -> None:
    source = transcript()
    for row in source.segments:
        row.text = "法輪功"
    model = TranslationModel()
    path = translate(model, "test", source, tmp_path, TranslationReview(POLICY, lambda _: None))
    cues = pysubs2.load(str(path))
    assert len(cues) == 3 and all(cue.plaintext == "***\n***" for cue in cues)


@pytest.mark.parametrize("stage", ["translation_v001", "translation_v002_filtered"])
def test_legacy_translation_reuses_response_without_paid_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    source = transcript()
    if stage == "translation_v002_filtered":
        legacy = tmp_path / "translation-review"
        legacy.mkdir()
        (legacy / "input.json").write_text(source.model_dump_json(), encoding="utf-8")
    content = Translation(
        segments=[
            TranslatedSegment(segment_id=row.segment_id, translation="既有译文")
            for row in source.segments
        ],
        warnings=[],
    ).model_dump_json()
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path)
        request = translation_request("test", source, stage)
        model.generate(request)
        translate(model, "test", source, tmp_path, TranslationReview(POLICY, model.replay))
        assert len(calls) == 1
        response = tmp_path / "production/test" / stage / "response.json"
        response.unlink()
        with pytest.raises(WorkflowError, match="不跨版本重复计费"):
            translate(model, "test", source, tmp_path, TranslationReview(POLICY, model.replay))
        assert len(calls) == 1


class WrongMaskModel(TranslationModel):
    def generate(self, request: GenerationRequest) -> str:
        return super().generate(request).replace("***", "**")


def test_model_cannot_change_star_counts(tmp_path: Path) -> None:
    source = transcript()
    source.segments[0].text = "習近平"
    with pytest.raises(WorkflowError, match="逐字星号"):
        translate(
            WrongMaskModel(), "test", source, tmp_path, TranslationReview(POLICY, lambda _: None)
        )
    assert not (tmp_path / "translation.json").exists()
