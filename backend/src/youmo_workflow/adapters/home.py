from pathlib import Path

import httpx
from pydantic import ValidationError

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import DiscoveryPage, DiscoveryVideo, ErrorResponse


class NodeHomeProvider:
    def __init__(self, token_path: Path, client: httpx.Client) -> None:
        self.token_path, self.client = token_path, client

    def page(self, cursor: str | None) -> DiscoveryPage:
        if not self.token_path.is_file():
            raise WorkflowError("NODE_NOT_CONFIGURED", "YouTube.js 内部令牌尚未配置。")
        body: dict[str, object] = {"profile_id": "default", "limit": 50}
        if cursor:
            body["continuation"] = cursor
        token = self.token_path.read_text(encoding="utf-8").strip()
        try:
            response = self.client.post(
                "http://127.0.0.1:8767/internal/v1/home-feed",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError:
            raise WorkflowError("NODE_UNAVAILABLE", "YouTube.js 服务连接失败。", True) from None
        if response.status_code != 200:
            raise safe_node_error(response)
        try:
            return DiscoveryPage.model_validate_json(response.content)
        except ValidationError:
            raise WorkflowError("NODE_SCHEMA_MISMATCH", "推荐页不符合结构契约。") from None

    def discover(self) -> DiscoveryPage:
        videos: dict[str, DiscoveryVideo] = {}
        cursor: str | None = None
        seen: set[str] = set()
        warnings: list[str] = []
        skipped = 0
        for _ in range(3):
            page = self.page(cursor)
            videos.update({video.video_id: video for video in page.items})
            warnings.extend(page.warnings)
            skipped += page.skipped_count
            cursor = page.continuation
            if cursor is None:
                break
            if cursor in seen:
                raise WorkflowError("CURSOR_LOOP", "推荐游标重复，停止读取。")
            seen.add(cursor)
        return DiscoveryPage(items=list(videos.values()), skipped_count=skipped, warnings=warnings)


def safe_node_error(response: httpx.Response) -> WorkflowError:
    allowed = {"PROFILE_NOT_CONFIGURED", "AUTH_REQUIRED", "CURSOR_EXPIRED", "COOKIE_FORMAT_INVALID"}
    code = "NODE_UNAVAILABLE"
    try:
        supplied = ErrorResponse.model_validate_json(response.content).code
        if supplied in allowed:
            code = supplied
    except ValidationError:
        pass
    return WorkflowError(
        code,
        "推荐来源不可用，请检查服务端登录配置；没有退回匿名首页。",
        response.status_code >= 500 or response.status_code == 429,
    )
