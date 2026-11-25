import http.cookiejar
from pathlib import Path

import httpx
import pytest

from youmo_workflow.adapters.acquisition import YoutubeAcquisition
from youmo_workflow.adapters.content_preparation import ProductionContent
from youmo_workflow.adapters.home import NodeHomeProvider
from youmo_workflow.adapters.initial_selection import V004InitialSelector
from youmo_workflow.adapters.production_model import ProductionModel
from youmo_workflow.adapters.rescue_selection import V008InitialSelector
from youmo_workflow.production_runtime import build_production
from youmo_workflow.runtime import Runtime
from youmo_workflow.worker import build_runner


def test_production_model_and_local_session_use_separate_transports(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WORKFLOW_DOWNLOAD_COOKIE_FILE", raising=False)
    calls: list[str] = []

    def local(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        calls.append("local")
        return httpx.Response(200, json={"cookie": "SID=synthetic-cookie", "revision": "fake"})

    def external(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.deepseek.com"
        assert "Authorization" not in request.headers
        calls.append("external")
        return httpx.Response(200)

    runtime.settings.node_token_path.parent.mkdir(parents=True, exist_ok=True)
    runtime.settings.node_token_path.write_text("synthetic-local-token")
    with (
        httpx.Client(transport=httpx.MockTransport(local)) as local_client,
        httpx.Client(transport=httpx.MockTransport(external)) as model_client,
    ):
        content, packaging, editor = build_production(
            runtime, local_client, model_client=model_client
        )
        model = content.providers.model
        assert isinstance(model, ProductionModel)
        assert model.client is model_client
        assert packaging.model is model and editor.model is model
        model.client.head("https://api.deepseek.com/")
        acquisition = content.providers.acquisition
        assert isinstance(acquisition, YoutubeAcquisition)
        assert acquisition.session_loader is not None
        acquisition.session_loader(http.cookiejar.CookieJar())
    assert calls == ["external", "local"]


@pytest.mark.parametrize("workbuddy", [False, True])
def test_selection_and_production_share_the_configured_model_client(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch, workbuddy: bool
) -> None:
    def fake_key(_path: Path) -> str:
        return "synthetic-key"

    def fake_channels(_path: Path) -> set[str]:
        return set()

    monkeypatch.setattr("youmo_workflow.worker.load_api_key", fake_key)
    monkeypatch.setattr("youmo_workflow.worker.known_channel_ids", fake_channels)
    if workbuddy:
        (runtime.settings.root / "config/model-route.txt").write_text("workbuddy", encoding="utf-8")
    transport = httpx.MockTransport(lambda _request: httpx.Response(500))
    with httpx.Client(transport=transport) as external, httpx.Client(transport=transport) as local:
        runner = build_runner(runtime, external, local)
        assert runner.selection_stages is not None
        initial = runner.selection_stages.providers.initial
        rescue = runner.selection_stages.providers.v008
        assert isinstance(initial, V004InitialSelector)
        assert isinstance(rescue, V008InitialSelector)
        expected = local if workbuddy else external
        assert initial.model.client is expected and rescue.model.client is expected
        content = runner.providers.content
        assert isinstance(content, ProductionContent)
        assert isinstance(content.providers.model, ProductionModel)
        assert content.providers.model.client is expected
        assert isinstance(runner.providers.home, NodeHomeProvider)
        assert runner.providers.home.client is local
