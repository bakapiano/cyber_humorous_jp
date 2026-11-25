import os
import re
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import JsonValue

from youmo_workflow.adapters.model_attempts import RepairTransport
from youmo_workflow.adapters.model_http import HttpRetryPolicy, RetryingCompletion
from youmo_workflow.adapters.model_records import completion_record, http_record, request_record
from youmo_workflow.adapters.model_repair import FormatRepair
from youmo_workflow.adapters.model_route import ModelRoute, read_workbuddy_key
from youmo_workflow.adapters.selection_models import (
    FrozenInitialCall,
    SavedCompletion,
)
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.model_policy import DEFAULT_REASONING_EFFORT
from youmo_workflow.execution_limits import limited


def read_deepseek_key(path: Path) -> str:
    value = os.environ.get("DEEPSEEK_API_KEY", "")
    if not value:
        try:
            value = path.read_text(encoding="utf-8-sig")
        except OSError:
            raise WorkflowError(
                "SELECTION_KEY_MISSING", "初筛模型凭据未配置；不会消耗调用额度。"
            ) from None
    found = re.search(r"sk-[A-Za-z0-9_-]+", value)
    if found is None:
        raise WorkflowError("SELECTION_KEY_INVALID", "初筛模型凭据格式无效。")
    return found.group(0)


class DeepSeekCompletion:
    def __init__(
        self,
        client: httpx.Client,
        key_path: Path,
        archive_root: Path | None = None,
        retry_policy: HttpRetryPolicy | None = None,
        *,
        route: ModelRoute = ModelRoute.DIRECT,
    ) -> None:
        self.client, self.key_path = client, key_path
        self.archive_root = archive_root
        self.route = route
        self.retry_policy = retry_policy if retry_policy and retry_policy.enabled else None

    def credential(self) -> str:
        if self.route is ModelRoute.WORKBUDDY:
            return read_workbuddy_key(self.key_path)
        return read_deepseek_key(self.key_path)

    def complete(self, call: FrozenInitialCall, key: str, resume: bool = False) -> SavedCompletion:
        payload = call.settings.model_dump()
        payload["reasoning_effort"] = DEFAULT_REASONING_EFFORT
        payload["messages"] = [{"role": "user", "content": call.prompt}]
        if key in call.prompt:
            raise WorkflowError("PRIVATE_MODEL_INPUT", "输入包含私有信息，未发出请求或保存载荷。")
        directory = request_record(
            self.archive_root, (call.request_sha256, call.prompt_sha256), payload
        )
        return self.invoke(payload, key, call.request_sha256, directory, resume)

    def checked(
        self,
        call: FrozenInitialCall,
        result: SavedCompletion,
        validate: Callable[[SavedCompletion], object],
        directory: Path,
    ) -> SavedCompletion:
        payload: dict[str, JsonValue] = call.settings.model_dump()
        payload["reasoning_effort"] = DEFAULT_REASONING_EFFORT
        payload["messages"] = [{"role": "user", "content": call.prompt}]
        return FormatRepair(
            directory,
            payload,
            RepairTransport(
                self.credential, self.invoke, self.resume if self.retry_policy else None
            ),
        ).run(result, validate)

    def resume(
        self, payload: dict[str, JsonValue], key: str, fingerprint: str, directory: Path
    ) -> SavedCompletion:
        return self.invoke(payload, key, fingerprint, directory, True)

    @limited("model")
    def invoke(
        self,
        payload: dict[str, JsonValue],
        key: str,
        fingerprint: str,
        directory: Path | None,
        resume: bool = False,
    ) -> SavedCompletion:
        if self.retry_policy is not None:
            if directory is None:
                raise WorkflowError("MODEL_RETRY_JOURNAL_REQUIRED", "网络恢复必须有持久化账本。")
            return RetryingCompletion(self.client, self.retry_policy, self.route).send(
                payload, key, fingerprint, directory, resume
            )
        if resume:
            raise WorkflowError("MODEL_RETRY_NOT_AUTHORIZED", "未启用模型网络重试。")
        try:
            response = self.route.post(self.client, key, payload, directory)
        except httpx.HTTPError:
            raise WorkflowError(
                "MODEL_ATTEMPT_UNRESOLVED", "模型请求中断或超时，可能已计费；不会自动重发。"
            ) from None
        http_record(directory, response, key)
        return completion_record(response.text, fingerprint, self.route)
