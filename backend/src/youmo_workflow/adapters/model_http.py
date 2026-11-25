import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field, JsonValue

from youmo_workflow.adapters.model_empty_response import (
    empty_protocol_response,
    note_empty_protocol,
)
from youmo_workflow.adapters.model_lock import model_lock
from youmo_workflow.adapters.model_records import (
    HttpReceipt,
    completion_record,
    http_record,
    recover_completion,
)
from youmo_workflow.adapters.model_route import DIRECT_URL, ModelRoute
from youmo_workflow.adapters.production_files import encode, freeze, save
from youmo_workflow.adapters.selection_models import SavedCompletion
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Contract


class HttpRetryPolicy(Contract):
    enabled: bool = False
    max_retries: Literal[2] = 2
    delay_seconds: int = Field(default=5, ge=0, le=20)
    authorization: str = ""


def load_retry_policy(root: Path) -> HttpRetryPolicy | None:
    path = root / "config/model-retry.json"
    if not path.exists():
        return None
    policy = HttpRetryPolicy.model_validate_json(path.read_bytes())
    return policy if policy.enabled else None


def retryable_status(status: int) -> bool:
    return status in {408, 425, 429} or 500 <= status <= 599


def last_http_status(directory: Path, retries: int) -> str:
    receipt = directory / f"network/{retries:02}/provider-http.json"
    if not receipt.exists():
        return ""
    status = HttpReceipt.model_validate_json(receipt.read_bytes()).status_code
    return f" 最近HTTP {status}。"


def recover_before_retry(
    directory: Path, fingerprint: str, enabled: bool
) -> SavedCompletion | None:
    http = directory / "provider-http.json"
    if (
        enabled
        and http.exists()
        and HttpReceipt.model_validate_json(http.read_bytes()).status_code != 200
    ):
        return None
    return recover_completion(directory, fingerprint)


@dataclass(frozen=True)
class CoverFallback:
    client: httpx.Client
    credential: Callable[[], str]

    @staticmethod
    def authorized(directory: Path, fingerprint: str, resume: bool) -> bool:
        marker = directory / "network/cover-fallback-authorized.txt"
        if marker.exists():
            return marker.read_text(encoding="utf-8") == fingerprint
        if resume or any((directory / "network").glob("*/attempt.json")):
            return False
        freeze(marker, fingerprint)
        return True


def used_cover_fallback(directory: Path) -> bool:
    return (directory / "network/01/cover-fallback.json").exists()


class RetryingCompletion:
    def __init__(
        self,
        client: httpx.Client,
        policy: HttpRetryPolicy,
        route: ModelRoute = ModelRoute.DIRECT,
        *,
        cover_fallback: CoverFallback | None = None,
    ) -> None:
        self.client, self.policy, self.route = client, policy, route
        self.cover_fallback = cover_fallback

    def send(
        self,
        payload: dict[str, JsonValue],
        key: str,
        fingerprint: str,
        directory: Path,
        resume: bool = False,
    ) -> SavedCompletion:
        with model_lock(directory / "network/request.lock"):
            cover_fallback = self.cover_fallback
            self.route.bind_retry_journal(directory, resume)
            start = self.start_index(directory, resume)
            fallback_allowed = (
                cover_fallback is not None
                and self.route is ModelRoute.WORKBUDDY
                and cover_fallback.authorized(directory, fingerprint, resume)
            )
            for index in range(start, self.policy.max_retries + 1):
                attempt = directory / f"network/{index:02}"
                if index and not (attempt / "attempt.json").exists():
                    time.sleep(self.policy.delay_seconds * index)
                result = self.once(attempt, payload, key, fingerprint)
                if result is not None:
                    accepted_index = index
                    if (
                        index == 0
                        and fallback_allowed
                        and cover_fallback is not None
                        and result.finish_reason == "content_filter"
                    ):
                        result = self.after_cover_filter(
                            directory, payload, fingerprint, result, cover_fallback
                        )
                        accepted_index = 1
                    save(
                        directory / "transport-result.json",
                        encode(
                            {
                                "accepted_attempt": accepted_index,
                                "request_sha256": fingerprint,
                                "retry_authorized": True,
                            }
                        ),
                    )
                    return result
            raise WorkflowError(
                "MODEL_HTTP_RETRIES_EXHAUSTED",
                "DeepSeek初次请求及两次网络恢复额度已用尽；资料保留，未继续循环调用。"
                + last_http_status(directory, self.policy.max_retries),
            )

    def after_cover_filter(
        self,
        directory: Path,
        payload: dict[str, JsonValue],
        fingerprint: str,
        refusal: SavedCompletion,
        fallback: CoverFallback,
    ) -> SavedCompletion:
        initial = directory / "network/00"
        if (
            not (initial / "provider-http.json").is_file()
            or not (initial / "wire-request.json").is_file()
            or recover_completion(initial, fingerprint) != refusal
        ):
            raise WorkflowError(
                "MODEL_FALLBACK_UNVERIFIED", "缺少可核验的内容筛选回执，不使用备用通道。"
            )
        target = directory / "network/01"
        if (target / "attempt.json").exists() and not used_cover_fallback(directory):
            raise WorkflowError("MODEL_FALLBACK_SLOT_USED", "备用通道的调用名额已使用。")
        freeze(
            target / "cover-fallback.json",
            encode(
                {
                    "url": DIRECT_URL,
                    "reason": "workbuddy_content_filter",
                    "primary_attempt": 0,
                    "request_sha256": fingerprint,
                }
            ),
        )
        direct = RetryingCompletion(fallback.client, self.policy)
        try:
            key = fallback.credential()
            if not key or key in encode(payload):
                raise WorkflowError("PRIVATE_MODEL_INPUT", "备用模型凭据或输入无效。")
            result = direct.once(target, payload, key, fingerprint)
        except WorkflowError as error:
            raise WorkflowError(
                "MODEL_FALLBACK_FAILED", f"DeepSeek 单次备用调用未完成：{error.message}"
            ) from None
        if result is None:
            raise WorkflowError(
                "MODEL_FALLBACK_UNRESOLVED", "DeepSeek 备用调用无可用回执，不再自动重发。"
            )
        return result

    def start_index(self, directory: Path, resume: bool) -> int:
        freeze(directory / "network/policy.json", self.policy.model_dump_json())
        legacy = directory / "network/legacy-initial.json"
        if (not resume and not legacy.exists()) or (directory / "network/00/attempt.json").exists():
            return 0
        http = directory / "provider-http.json"
        if http.exists() and not retryable_status(
            HttpReceipt.model_validate_json(http.read_bytes()).status_code
        ):
            raise WorkflowError(
                "MODEL_HTTP_NON_RETRYABLE", "历史模型响应不是可恢复的HTTP错误，未重复相同请求。"
            )
        freeze(
            directory / "network/legacy-initial.json",
            encode(
                {
                    "initial_attempt_preexists": True,
                    "original_records_unchanged": True,
                    "authorization": self.policy.authorization,
                }
            ),
        )
        return 1

    def once(
        self, directory: Path, payload: dict[str, JsonValue], key: str, fingerprint: str
    ) -> SavedCompletion | None:
        freeze(directory / "payload.json", encode(payload))
        result = self.cached(directory, fingerprint)
        if result is not None:
            return result
        try:
            with (directory / "attempt.json").open("x", encoding="utf-8") as stream:
                stream.write(encode({"max_calls": 1, "request_sha256": fingerprint}))
        except FileExistsError:
            return None
        try:
            response = self.route.post(self.client, key, payload, directory)
        except httpx.HTTPError as error:
            save(
                directory / "transport-error.json",
                encode({"exception_type": type(error).__name__, "retry_authorized": True}),
            )
            return None
        try:
            http_record(directory, response, key)
            result = completion_record(response.text, fingerprint, self.route)
        except WorkflowError as error:
            if error.code == "MODEL_HTTP_ERROR" and retryable_status(response.status_code):
                return None
            if error.code == "MODEL_RESPONSE_INVALID" and empty_protocol_response(directory):
                note_empty_protocol(directory)
                return None
            save(directory / "terminal-error.json", encode({"code": error.code}))
            raise
        save(directory / "response.json", result.model_dump_json())
        return result

    def cached(self, directory: Path, fingerprint: str) -> SavedCompletion | None:
        terminal = directory / "terminal-error.json"
        if terminal.exists():
            if json.loads(terminal.read_bytes()).get(
                "code"
            ) == "MODEL_RESPONSE_INVALID" and empty_protocol_response(directory):
                note_empty_protocol(directory)
                return None
            raise WorkflowError(
                "MODEL_HTTP_NON_RETRYABLE", "此前响应已明确不可重复相同请求，未重发。"
            )
        result_path = directory / "response.json"
        if result_path.exists():
            result = SavedCompletion.model_validate_json(result_path.read_bytes())
            if result.request_sha256 != fingerprint:
                raise WorkflowError("PRODUCTION_INPUT_CHANGED", "网络重试响应指纹不匹配。")
            return result
        http = directory / "provider-http.json"
        if not http.exists():
            return None
        receipt = HttpReceipt.model_validate_json(http.read_bytes())
        if receipt.status_code == 200:
            if empty_protocol_response(directory):
                note_empty_protocol(directory)
                return None
            return recover_completion(directory, fingerprint)
        if not retryable_status(receipt.status_code):
            raise WorkflowError(
                "MODEL_HTTP_NON_RETRYABLE", f"模型HTTP {receipt.status_code} 不适合重复相同请求。"
            )
        return None
