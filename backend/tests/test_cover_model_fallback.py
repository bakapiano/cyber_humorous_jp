import json
from pathlib import Path

import httpx
import pytest

from youmo_workflow.adapters.model_http import CoverFallback, HttpRetryPolicy
from youmo_workflow.adapters.model_records import recover_completion
from youmo_workflow.adapters.model_route import ModelRoute
from youmo_workflow.adapters.production_model import ProductionModel
from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.errors import WorkflowError

PLAN = "cover_plan_raw_history_v001"
VISION = "cover_vision_raw_history_v001"
KEY_LOCAL = "synthetic-gateway-test"
KEY_DIRECT = "synthetic-direct-test"


def reply(model: str, reason: str, content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"finish_reason": reason, "message": {"content": content}}],
        },
    )


def filtered(_request: httpx.Request) -> httpx.Response:
    return reply("deepseek-v4.1-flash", "content_filter", "synthetic refusal")


def model_for(
    root: Path, primary: httpx.Client, direct: httpx.Client | None = None
) -> ProductionModel:
    key_path = root / "synthetic-config.json"
    key_path.write_text(json.dumps({"api_key": KEY_LOCAL}), encoding="utf-8")
    model = ProductionModel(
        primary,
        key_path,
        root,
        HttpRetryPolicy(enabled=True, delay_seconds=0),
        route=ModelRoute.WORKBUDDY,
    )
    if direct is not None:
        model.cover_fallback = CoverFallback(direct, lambda: KEY_DIRECT)
    return model


def unused(_request: httpx.Request) -> httpx.Response:
    pytest.fail("No additional network request was expected")


@pytest.mark.parametrize("stage", [PLAN, VISION])
def test_fallback_keeps_refusal_and_same_payload(tmp_path: Path, stage: str) -> None:
    calls: list[httpx.Request] = []

    def primary(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host == "127.0.0.1"
        assert request.headers["Authorization"] == f"Bearer {KEY_LOCAL}"
        return filtered(request)

    def direct(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.host == "api.deepseek.com"
        assert request.headers["Authorization"] == f"Bearer {KEY_DIRECT}"
        return reply("deepseek-flash", "stop", "{}")

    request = GenerationRequest(content_id="sample", stage=stage, prompt="synthetic cover")
    with httpx.Client(transport=httpx.MockTransport(primary)) as wb:
        with httpx.Client(transport=httpx.MockTransport(direct)) as ds:
            model = model_for(tmp_path, wb, ds)
            assert model.generate_checked(request, json.loads) == "{}"
            assert model.generate_checked(request, json.loads) == "{}"
    assert len(calls) == 2
    before = json.loads(calls[0].content)
    after = json.loads(calls[1].content)
    assert before.pop("model") == "cn:deepseek-v4.1-flash"
    assert after.pop("model") == "deepseek-flash"
    assert before == after
    stage_path = tmp_path / "production/sample" / stage
    fingerprint = (stage_path / "fingerprint.txt").read_text(encoding="utf-8")
    original = recover_completion(stage_path / "network/00", fingerprint)
    assert original is not None and original.finish_reason == "content_filter"
    assert (stage_path / "network/01/cover-fallback.json").exists()
    assert json.loads((stage_path / "transport-result.json").read_bytes())["accepted_attempt"] == 1
    assert not (stage_path / "network/02").exists()


def test_previous_refusal_and_title_stage_are_not_rerouted(tmp_path: Path) -> None:
    historical = GenerationRequest(content_id="history", stage=PLAN, prompt="synthetic")
    title = GenerationRequest(content_id="new", stage="title_raw_history_v001", prompt="synthetic")
    with httpx.Client(transport=httpx.MockTransport(filtered)) as wb:
        with httpx.Client(transport=httpx.MockTransport(unused)) as ds:
            with pytest.raises(WorkflowError, match="停止原因"):
                model_for(tmp_path, wb).generate_checked(historical, json.loads)
            for request in (historical, title):
                with pytest.raises(WorkflowError, match="停止原因"):
                    model_for(tmp_path, wb, ds).generate_checked(request, json.loads)
    assert not list(tmp_path.rglob("cover-fallback.json"))


@pytest.mark.parametrize("status", [401, 402, 429, 503])
def test_http_errors_do_not_start_fallback(tmp_path: Path, status: int) -> None:
    def rejected(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "synthetic failure"})

    request = GenerationRequest(content_id="sample", stage=PLAN, prompt="synthetic")
    with httpx.Client(transport=httpx.MockTransport(rejected)) as wb:
        with httpx.Client(transport=httpx.MockTransport(unused)) as ds:
            with pytest.raises(WorkflowError):
                model_for(tmp_path, wb, ds).generate_checked(request, json.loads)
    assert not list(tmp_path.rglob("cover-fallback.json"))


def test_fallback_timeout_is_not_replayed_after_restart(tmp_path: Path) -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    request = GenerationRequest(content_id="sample", stage=PLAN, prompt="synthetic")
    with httpx.Client(transport=httpx.MockTransport(filtered)) as wb:
        with httpx.Client(transport=httpx.MockTransport(timeout)) as ds:
            with pytest.raises(WorkflowError, match="无可用回执"):
                model_for(tmp_path, wb, ds).generate_checked(request, json.loads)
    with httpx.Client(transport=httpx.MockTransport(unused)) as unused_client:
        with pytest.raises(WorkflowError, match="无可用回执"):
            model_for(tmp_path, unused_client, unused_client).generate_checked(request, json.loads)


def test_filter_after_an_uncertain_primary_attempt_does_not_fall_back(tmp_path: Path) -> None:
    calls = 0

    def primary(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        return filtered(request)

    request = GenerationRequest(content_id="sample", stage=PLAN, prompt="synthetic")
    with httpx.Client(transport=httpx.MockTransport(primary)) as wb:
        with httpx.Client(transport=httpx.MockTransport(unused)) as ds:
            with pytest.raises(WorkflowError, match="停止原因"):
                model_for(tmp_path, wb, ds).generate_checked(request, json.loads)
    assert calls == 2
    assert not list(tmp_path.rglob("cover-fallback.json"))


def test_invalid_fallback_result_does_not_get_a_correction(tmp_path: Path) -> None:
    def invalid(_request: httpx.Request) -> httpx.Response:
        return reply("deepseek-flash", "length", "{}")

    request = GenerationRequest(content_id="sample", stage=VISION, prompt="synthetic")
    with httpx.Client(transport=httpx.MockTransport(filtered)) as wb:
        with httpx.Client(transport=httpx.MockTransport(invalid)) as ds:
            with pytest.raises(WorkflowError, match="未通过校验"):
                model_for(tmp_path, wb, ds).generate_checked(request, json.loads)
    assert not list(tmp_path.rglob("corrections"))
