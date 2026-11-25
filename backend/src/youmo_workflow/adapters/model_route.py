"""Model transport selection, independent of immutable workflow request fingerprints."""

import os
from enum import StrEnum
from pathlib import Path

import httpx
from pydantic import BaseModel, JsonValue, SecretStr, ValidationError

from youmo_workflow.adapters.production_files import encode, freeze
from youmo_workflow.domain.errors import WorkflowError

DIRECT_URL = "https://api.deepseek.com/chat/completions"
WORKBUDDY_URL = "http://127.0.0.1:7863/v1/chat/completions"
WORKBUDDY_MODEL = "cn:deepseek-v4.1-flash"


class GatewayCredential(BaseModel):
    api_key: SecretStr


class ModelRoute(StrEnum):
    DIRECT = "deepseek"
    WORKBUDDY = "workbuddy"

    def credential_path(self, root: Path) -> Path:
        if self is ModelRoute.WORKBUDDY:
            return Path(os.environ.get("WORKFLOW_WORKBUDDY_CONFIG", "D:/eh/wb2api/config.json"))
        return root.parent / "ds_key"

    def bind_retry_journal(self, directory: Path, resume: bool) -> None:
        marker = directory / "network/model-route.txt"
        if marker.exists():
            if marker.read_text(encoding="utf-8") != self.value:
                raise WorkflowError("MODEL_ROUTE_CHANGED", "既有模型调用不能切换通道重发。")
        elif self is ModelRoute.WORKBUDDY:
            if resume or any((directory / "network").glob("*/attempt.json")):
                raise WorkflowError("MODEL_ROUTE_CHANGED", "旧通道已记账，不切换通道重发。")
            freeze(marker, self.value)

    def post(
        self, client: httpx.Client, key: str, payload: dict[str, JsonValue], directory: Path | None
    ) -> httpx.Response:
        wire = payload
        url = DIRECT_URL
        if self is ModelRoute.WORKBUDDY:
            if directory is None or payload.get("model") != "deepseek-flash":
                raise WorkflowError("MODEL_ROUTE_INVALID", "模型通道或持久化账本不匹配。")
            limit = payload.get("max_tokens")
            if type(limit) is not int:
                raise WorkflowError("MODEL_ROUTE_INVALID", "模型输出上限无效。")
            wire = {**payload, "model": WORKBUDDY_MODEL, "max_tokens": min(limit, 128000)}
            url = WORKBUDDY_URL
            freeze(
                directory / "wire-request.json",
                encode({"url": url, "model": WORKBUDDY_MODEL, "max_tokens": wire["max_tokens"]}),
            )
        return client.post(
            url,
            headers={"Authorization": f"Bearer {key}"},
            json=wire,
            timeout=httpx.Timeout(1200, connect=15),
            follow_redirects=False,
        )

    def receipt_model(self, model: str) -> str:
        if self is ModelRoute.DIRECT:
            return model
        if model not in {WORKBUDDY_MODEL, "deepseek-v4.1-flash"}:
            raise WorkflowError("MODEL_OUTPUT_INVALID", "WorkBuddy回执模型与已授权模型不符。")
        # Workflow validators use the frozen logical name. Raw receipts keep the actual model.
        return "deepseek-flash"


def load_model_route(root: Path) -> ModelRoute:
    path = root / "config/model-route.txt"
    return (
        ModelRoute(path.read_text(encoding="utf-8").strip()) if path.exists() else ModelRoute.DIRECT
    )


def read_workbuddy_key(path: Path) -> str:
    try:
        key = GatewayCredential.model_validate_json(path.read_bytes()).api_key.get_secret_value()
    except (OSError, ValidationError):
        raise WorkflowError("SELECTION_KEY_MISSING", "WorkBuddy网关凭据不可用。") from None
    if not key or any(not 33 <= ord(char) <= 126 for char in key):
        raise WorkflowError("SELECTION_KEY_INVALID", "WorkBuddy网关凭据格式无效。")
    return key
