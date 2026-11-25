from pathlib import Path

import httpx
from pydantic import ValidationError

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import DiscoveryPage


async def home_feed(token_path: Path) -> DiscoveryPage:
    raw = await node_request(
        token_path, "POST", "/home-feed", {"profile_id": "default", "limit": 30}
    )
    try:
        return DiscoveryPage.model_validate_json(raw)
    except ValidationError:
        raise WorkflowError("NODE_SCHEMA_MISMATCH", "推荐服务返回不符合类型契约。") from None


async def node_request(
    token_path: Path, method: str, path: str, payload: dict[str, object] | None = None
) -> bytes:
    if not token_path.is_file():
        raise WorkflowError("NODE_NOT_CONFIGURED", "YouTube.js 内部服务尚未初始化。")
    token = token_path.read_text(encoding="utf-8").strip()
    try:
        async with httpx.AsyncClient(timeout=90, trust_env=False) as client:
            response = await client.request(
                method,
                "http://127.0.0.1:8767/internal/v1" + path,
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError:
        raise WorkflowError("NODE_UNAVAILABLE", "YouTube.js 服务未运行或连接失败。", True) from None
    if response.status_code != 200:
        raise WorkflowError("NODE_REQUEST_FAILED", "登录服务请求未完成，请稍后重试或检查本地服务。")
    return response.content
