import http.cookiejar
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import now_utc, stamp

RETRY_DELAYS = (2, 4, 8, 16, 30)
SAFE_CODES = frozenset(
    {
        "SESSION_REFRESHING",
        "SESSION_VERIFY_UNAVAILABLE",
        "AUTH_REQUIRED",
        "PROFILE_BUSY",
        "SESSION_CHANGED",
        "INTERNAL_UNAUTHORIZED",
        "BROWSER_ACCESS_DENIED",
        "UPSTREAM_UNAVAILABLE",
    }
)


class DownloadSession(BaseModel):
    cookie: SecretStr
    revision: str


class SessionFailure(BaseModel):
    code: str


class NodeDownloadSession:
    def __init__(
        self,
        token_path: Path,
        client: httpx.Client,
        *,
        wait: Callable[[float], None] = time.sleep,
        journal: Path | None = None,
    ) -> None:
        self.token_path, self.client = token_path, client
        self.wait, self.journal = wait, journal

    def load(self, jar: http.cookiejar.CookieJar) -> None:
        try:
            token = self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            raise WorkflowError("SOURCE_SESSION_CONFIG_ERROR", "本机会话服务凭据不可读。") from None
        operation = uuid4().hex
        for attempt in range(1, len(RETRY_DELAYS) + 2):
            try:
                session = self.read_once(token)
                load_header(jar, session.cookie.get_secret_value())
            except WorkflowError as error:
                delay = (
                    RETRY_DELAYS[attempt - 1]
                    if error.retryable and attempt <= len(RETRY_DELAYS)
                    else None
                )
                self.record(operation, attempt, error.code, error.message, delay)
                if delay is None:
                    code = "SOURCE_SESSION_RETRIES_EXHAUSTED" if error.retryable else error.code
                    raise WorkflowError(
                        code, f"{error.message} 已读取{attempt}次；记录ID={operation}。"
                    ) from None
                self.wait(delay)
            else:
                self.record(operation, attempt, "OK", "HTTP 200", None)
                return

    def read_once(self, token: str) -> DownloadSession:
        try:
            response = self.client.get(
                "http://127.0.0.1:8767/internal/v1/auth/download-session",
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=False,
                timeout=10,
            )
        except httpx.HTTPError:
            raise WorkflowError(
                "SOURCE_SESSION_NETWORK_ERROR", "本机会话服务连接超时或中断。", retryable=True
            ) from None
        if response.status_code != 200:
            raise response_error(response)
        try:
            if len(response.content) > 20000:
                raise ValueError("Oversized session")
            return DownloadSession.model_validate_json(response.content)
        except (ValueError, ValidationError):
            raise WorkflowError(
                "SOURCE_SESSION_INVALID", "会话服务响应格式无效；未记录私有正文。"
            ) from None

    def record(
        self, operation: str, attempt: int, code: str, detail: str, delay: int | None
    ) -> None:
        if self.journal is None:
            return
        event = {
            "at": stamp(now_utc()),
            "operation": operation,
            "attempt": attempt,
            "code": code,
            "detail": detail,
            "retry_after_seconds": delay,
        }
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        with self.journal.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def response_error(response: httpx.Response) -> WorkflowError:
    remote_code = "UNEXPECTED_RESPONSE"
    if len(response.content) <= 20000:
        try:
            candidate = SessionFailure.model_validate_json(response.content).code
            remote_code = candidate if candidate in SAFE_CODES else remote_code
        except ValidationError:
            pass
    detail = f"HTTP {response.status_code}/{remote_code}"
    if response.status_code in {401, 403}:
        return WorkflowError("SOURCE_SESSION_AUTH_FAILED", f"本机会话服务鉴权被拒绝（{detail}）。")
    if remote_code == "AUTH_REQUIRED":
        return WorkflowError("SOURCE_LOGIN_REQUIRED", f"YouTube需要登录或同步会话（{detail}）。")
    transient = (
        response.status_code in {408, 425, 429}
        or response.status_code >= 500
        or remote_code in {"SESSION_REFRESHING", "PROFILE_BUSY", "SESSION_CHANGED"}
    )
    return WorkflowError(
        "SOURCE_SESSION_UNAVAILABLE", f"YouTube会话暂不可用（{detail}）。", retryable=transient
    )


def load_header(jar: http.cookiejar.CookieJar, header: str) -> None:
    if not header or len(header) > 16384 or re.search(r"[^\x20-\x7e]", header):
        raise WorkflowError("SOURCE_SESSION_INVALID", "下载会话格式无效。")
    pairs: dict[str, str] = {}
    for pair in header.split(";"):
        found = re.fullmatch(r"\s*([A-Za-z0-9_-]+)=([^;\s]+)", pair)
        if not found or found[1] in pairs:
            raise WorkflowError("SOURCE_SESSION_INVALID", "下载会话格式无效。")
        pairs[found[1]] = found[2]
    for name, value in pairs.items():
        jar.set_cookie(
            http.cookiejar.Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=".youtube.com",
                domain_specified=True,
                domain_initial_dot=True,
                path="/",
                path_specified=True,
                secure=True,
                expires=None,
                discard=True,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": ""},
                rfc2109=False,
            )
        )
