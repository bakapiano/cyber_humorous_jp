import base64
import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import JsonValue

from youmo_workflow.adapters.model_attempts import RepairTransport
from youmo_workflow.adapters.model_http import (
    CoverFallback,
    HttpRetryPolicy,
    RetryingCompletion,
    recover_before_retry,
    used_cover_fallback,
)
from youmo_workflow.adapters.model_records import completion_record, http_record
from youmo_workflow.adapters.model_repair import FormatRepair
from youmo_workflow.adapters.model_route import ModelRoute, read_workbuddy_key
from youmo_workflow.adapters.production_files import digest, encode, folder, freeze, save
from youmo_workflow.adapters.selection_completion import read_deepseek_key
from youmo_workflow.adapters.selection_models import SavedCompletion
from youmo_workflow.domain.content import GenerationRequest
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.model_policy import DEFAULT_REASONING_EFFORT
from youmo_workflow.execution_limits import limited, resource_budget

COVER_FALLBACK_STAGES = {"cover_plan_raw_history_v001", "cover_vision_raw_history_v001"}


class ProductionModel:
    def __init__(
        self,
        client: httpx.Client,
        key_path: Path,
        journal_root: Path,
        retry_policy: HttpRetryPolicy | None = None,
        *,
        route: ModelRoute = ModelRoute.DIRECT,
    ) -> None:
        self.client, self.key_path, self.journal_root = client, key_path, journal_root
        self.route = route
        self.cover_fallback: CoverFallback | None = None
        self.retry_policy = retry_policy if retry_policy and retry_policy.enabled else None

    def credential(self) -> str:
        if self.route is ModelRoute.WORKBUDDY:
            return read_workbuddy_key(self.key_path)
        return read_deepseek_key(self.key_path)

    def cached(self, request: GenerationRequest) -> str | None:
        directory = self.journal_root / "production" / request.content_id / request.stage
        path = directory / "response.json"
        if not path.exists():
            return None
        frozen = GenerationRequest.model_validate_json((directory / "request.json").read_bytes())
        saved = SavedCompletion.model_validate_json(path.read_bytes())
        fingerprint = hashlib.sha256(encode(model_payload(request)).encode()).hexdigest()
        if frozen != request or saved.request_sha256 != fingerprint:
            raise WorkflowError("PRODUCTION_INPUT_CHANGED", "旧剪辑响应与当前输入不匹配。")
        if saved.finish_reason != "stop" or not saved.content:
            raise WorkflowError("MODEL_OUTPUT_INVALID", "旧剪辑响应不完整，不能复用。")
        return saved.content

    def replay(self, request: GenerationRequest) -> str | None:
        result = self.cached(request)
        directory = self.journal_root / "production" / request.content_id / request.stage
        if result is None and (directory / "attempt.json").exists():
            raise WorkflowError(
                "MODEL_ATTEMPT_UNRESOLVED", "旧阶段已有调用记录，不跨版本重复计费。"
            )
        return result

    def generate_checked(
        self, request: GenerationRequest, validate: Callable[[str], object]
    ) -> str:
        directory = folder(self.journal_root, request.content_id, request.stage)
        try:
            self.generate(request)
        except WorkflowError as error:
            if error.code != "MODEL_OUTPUT_INVALID" or not (directory / "response.json").exists():
                raise
        initial = SavedCompletion.model_validate_json((directory / "response.json").read_bytes())
        if (
            initial.request_sha256
            != hashlib.sha256(encode(model_payload(request)).encode()).hexdigest()
        ):
            raise WorkflowError("PRODUCTION_INPUT_CHANGED", "初次响应指纹不匹配，未执行纠正。")
        repair = FormatRepair(
            directory,
            model_payload(request),
            RepairTransport(
                self.credential,
                self.invoke,
                self.resume if self.retry_policy else None,
            ),
        )
        result = repair.run(
            initial,
            lambda item: validate(item.content or ""),
            allow_corrections=not used_cover_fallback(directory),
        )
        assert result.content is not None
        return result.content

    def generate(self, request: GenerationRequest) -> str:
        directory = folder(self.journal_root, request.content_id, request.stage)
        payload = model_payload(request)
        serialized = encode(payload)
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        freeze(directory / "request.json", request.model_dump_json())
        freeze(directory / "fingerprint.txt", fingerprint)
        freeze(directory / "payload.json", serialized)
        result_path = directory / "response.json"
        if result_path.exists():
            saved = SavedCompletion.model_validate_json(result_path.read_bytes())
        else:
            saved = recover_before_retry(directory, fingerprint, self.retry_policy is not None)
            if saved is None:
                fallback = self.cover_fallback if request.stage in COVER_FALLBACK_STAGES else None
                saved = self.request_once(directory, payload, fingerprint, serialized, fallback)
            save(result_path, saved.model_dump_json())
        if (
            saved.request_sha256 != fingerprint
            or saved.finish_reason != "stop"
            or not saved.content
        ):
            raise WorkflowError("MODEL_OUTPUT_INVALID", "已有响应不完整或不匹配，不自动重复计费。")
        return saved.content

    def request_once(
        self,
        directory: Path,
        payload: dict[str, JsonValue],
        fingerprint: str,
        serialized: str,
        fallback: CoverFallback | None = None,
    ) -> SavedCompletion:
        key = self.credential()
        if key in serialized:
            raise WorkflowError("PRIVATE_MODEL_INPUT", "输入包含私有信息，未发出请求。")
        resume = False
        try:
            reserve_attempt(directory, fingerprint)
        except WorkflowError:
            if self.retry_policy is None:
                raise
            resume = True
        if self.retry_policy is not None and fallback is not None:
            transport = RetryingCompletion(
                self.client, self.retry_policy, self.route, cover_fallback=fallback
            )
            with resource_budget().acquire("model"):
                return transport.send(payload, key, fingerprint, directory, resume)
        return self.invoke(payload, key, fingerprint, directory, resume)

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
        directory: Path | None = None,
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
                "MODEL_ATTEMPT_UNRESOLVED", "模型请求超时或中断，未自动重发。"
            ) from None
        http_record(directory, response, key)
        return completion_record(response.text, fingerprint, self.route)


def reserve_attempt(directory: Path, fingerprint: str) -> None:
    try:
        with (directory / "attempt.json").open("x", encoding="utf-8") as stream:
            stream.write(encode({"max_calls": 1, "request_sha256": fingerprint}))
    except FileExistsError:
        raise WorkflowError(
            "MODEL_ATTEMPT_UNRESOLVED", "本阶段已有调用记录且无可复用响应，不自动重复计费。"
        ) from None


def model_payload(request: GenerationRequest) -> dict[str, JsonValue]:
    content: list[JsonValue] = [{"type": "text", "text": request.prompt}]
    for frame in request.images:
        if digest(frame.path) != frame.sha256 or frame.path.stat().st_size > 8000000:
            raise WorkflowError("FRAME_CHANGED", "模型图像缺失、过大或哈希已改变。")
        content.append({"type": "text", "text": frame.label})
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64,"
                    + base64.b64encode(frame.path.read_bytes()).decode()
                },
            }
        )
    return {
        "model": "deepseek-flash",
        "thinking": {"type": "enabled"},
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "max_tokens": 65536,
        "stream": False,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": content if request.images else request.prompt}],
    }
