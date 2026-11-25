import hashlib
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from selection_support import completion, initial_request

from youmo_workflow.adapters.production_files import encode
from youmo_workflow.adapters.production_model import ProductionModel, model_payload
from youmo_workflow.adapters.rescue_composition import rescue_call
from youmo_workflow.adapters.selection_completion import DeepSeekCompletion
from youmo_workflow.adapters.selection_history import HistoryInputs
from youmo_workflow.adapters.selection_models import (
    CompletionUsage,
    CurrentEntry,
    ExampleTrace,
    ModelSettings,
    SavedCompletion,
)
from youmo_workflow.adapters.selection_profile import V004Profile
from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.selection import InitialRequest, ModelCandidate, ModelExample


@pytest.mark.parametrize(
    "stage",
    [
        "translation_v003_masked",
        "title_raw_history_v001",
        "cover_plan_raw_history_v001",
        "cover_vision_raw_history_v001",
        "editing_v004_hardsub",
        "subtitle_llm_review_hardsub_v003",
    ],
)
def test_every_new_production_stage_uses_high_without_changing_other_settings(stage: str) -> None:
    payload = model_payload(GenerationRequest(content_id="new", stage=stage, prompt="source"))
    assert payload["reasoning_effort"] == "high"
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["model"] == "deepseek-flash" and payload["max_tokens"] == 65536


class ProfileWithFixtureHistory(V004Profile):
    def history(
        self, entry: CurrentEntry, request: InitialRequest
    ) -> tuple[HistoryInputs, dict[str, str]]:
        return HistoryInputs([], {}, set()), {}


def example_fixture(
    _day: date, _candidates: list[ModelCandidate], _history: HistoryInputs
) -> tuple[list[ModelExample], list[ExampleTrace]]:
    return [ModelExample(example_id="one", title="synthetic example")], []


def test_new_selection_and_rescue_freeze_high_without_editing_research_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = ModelSettings(reasoning_effort="max")
    source = tmp_path / "settings.json"
    source.write_text(settings.model_dump_json(), encoding="utf-8")
    before = source.read_bytes()
    entry = CurrentEntry(
        prompt_version="v008",
        prompt_template="unused",
        config_path=str(source),
        dataset_path="unused",
        rank_count=50,
        daily_shortlist_k=30,
    )
    monkeypatch.setattr(
        "youmo_workflow.adapters.selection_profile.retrieve_examples", example_fixture
    )
    call = ProfileWithFixtureHistory(tmp_path).build_call(
        initial_request(60), entry, settings, b"{{candidates_json}}"
    )
    call = call.model_copy(update={"version": "v008", "rescue_template": "{{candidates_json}}"})
    rescue = rescue_call(call, completion(call))
    sent: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["reasoning_effort"])
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        provider = DeepSeekCompletion(client, tmp_path / "unused")
        for request in (call, rescue, call.model_copy(update={"settings": settings})):
            assert request.settings.max_tokens == 131072
            provider.complete(request, "sk-test")
    assert sent == ["high", "high", "high"] and source.read_bytes() == before
    assert settings.reasoning_effort == "max"
    assert ModelSettings().reasoning_effort == "high"


@pytest.mark.parametrize("completed", [True, False])
def test_historical_max_fingerprint_never_becomes_a_new_high_attempt(
    tmp_path: Path, completed: bool
) -> None:
    request = GenerationRequest(content_id="old", stage="title_raw_history_v001", prompt="source")
    directory = tmp_path / "production/old/title_raw_history_v001"
    directory.mkdir(parents=True)
    old_payload = model_payload(request)
    old_payload["reasoning_effort"] = "max"
    payload = encode(old_payload)
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    (directory / "request.json").write_text(request.model_dump_json(), encoding="utf-8")
    (directory / "payload.json").write_text(payload, encoding="utf-8")
    (directory / "fingerprint.txt").write_text(fingerprint, encoding="utf-8")
    (directory / "attempt.json").write_text('{"max_calls":1}', encoding="utf-8")
    if completed:
        saved = SavedCompletion(
            request_sha256=fingerprint,
            model="deepseek-flash",
            content="{}",
            finish_reason="stop",
            usage=CompletionUsage(),
        )
        (directory / "response.json").write_text(saved.model_dump_json(), encoding="utf-8")
    before = {path: path.read_bytes() for path in directory.iterdir()}

    def no_network(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Policy switch must not issue an old paid request")

    with httpx.Client(transport=httpx.MockTransport(no_network)) as client:
        model = ProductionModel(client, tmp_path / "unused", tmp_path)
        if completed:
            with pytest.raises(WorkflowError):
                model.cached(request)
        with pytest.raises(WorkflowError):
            model.generate(request)
    assert all(path.read_bytes() == raw for path, raw in before.items())
