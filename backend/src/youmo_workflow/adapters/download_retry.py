"""Bounded, durable retries of source reads after explicit TLS EOF failures only."""

import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from youmo_workflow.adapters.production_files import save
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import now_utc, parse_stamp, stamp

DELAYS = (5, 15)


def retryable_tls(error: Exception) -> bool:
    if isinstance(error, WorkflowError):
        return False
    message = str(error)
    if re.search(
        r"certificate|cert_verify|sign in|not a bot|login required|captcha|confirm your age|"
        r"cookie|HTTP(?: Error)?\s+[45][0-9]{2}\b",
        message,
        re.I,
    ):
        return False
    return bool(
        re.search(r"UNEXPECTED_EOF_WHILE_READING|EOF occurred in violation of protocol", message)
    )


class Attempt(BaseModel):
    started_at: str
    status: Literal["running", "tls_eof", "terminal", "succeeded"] = "running"
    next_at: str | None = None


class Ledger(BaseModel):
    version: Literal["source_tls_retry_v1"] = "source_tls_retry_v1"
    video_id: str
    attempts: list[Attempt] = Field(default_factory=list[Attempt], max_length=3)


def read_ledger(path: Path, video_id: str) -> Ledger:
    ledger = (
        Ledger.model_validate_json(path.read_bytes())
        if path.exists()
        else Ledger(video_id=video_id)
    )
    if ledger.video_id != video_id:
        raise WorkflowError("DOWNLOAD_RETRY_ID_CHANGED", "下载重试账本与素材身份不一致。")
    if ledger.attempts and ledger.attempts[-1].status != "tls_eof":
        raise WorkflowError(
            "DOWNLOAD_RETRY_NOT_ALLOWED", "已有终态或未确认的下载尝试；保留账本，不重新执行。"
        )
    if len(ledger.attempts) >= 3:
        raise WorkflowError("SOURCE_DOWNLOAD_UNAVAILABLE", "原片TLS断连重试次数已用尽。")
    return ledger


def download_with_retry[T](
    directory: Path,
    video_id: str,
    operation: Callable[[], T],
    *,
    wait: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = now_utc,
) -> T:
    path = directory / "download-transport.json"
    ledger = read_ledger(path, video_id)
    while True:
        if ledger.attempts and (next_at := ledger.attempts[-1].next_at):
            seconds = max(0.0, (parse_stamp(next_at) - clock()).total_seconds())
            if seconds > 60:
                raise WorkflowError(
                    "DOWNLOAD_RETRY_CLOCK_INVALID", "下载退避时钟异常，未提前重试。"
                )
            if seconds:
                wait(seconds)
        attempt = Attempt(started_at=stamp(clock()))
        ledger.attempts.append(attempt)
        save(path, ledger.model_dump_json())
        try:
            result = operation()
        except Exception as error:
            attempt.status = "tls_eof" if retryable_tls(error) else "terminal"
            retry = attempt.status == "tls_eof" and len(ledger.attempts) < 3
            if retry:
                attempt.next_at = stamp(
                    clock() + timedelta(seconds=DELAYS[len(ledger.attempts) - 1])
                )
            save(path, ledger.model_dump_json())
            if not retry:
                raise
        else:
            attempt.status = "succeeded"
            save(path, ledger.model_dump_json())
            return result
