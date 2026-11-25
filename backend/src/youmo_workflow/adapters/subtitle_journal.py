import hashlib
import re
import time
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import JsonValue

from youmo_workflow.adapters.biliup_receipts import redaction_values, scrub_text
from youmo_workflow.adapters.production_files import encode, freeze, save
from youmo_workflow.domain.errors import WorkflowError


class SubtitleJournal:
    def __init__(self, root: Path | None, credentials: Path, bvid: str, cid: int):
        if not re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid) or cid <= 0:
            raise WorkflowError("INVALID_REMOTE_ID", "字幕目标身份无效。")
        self.bvid, self.cid, self.started = bvid, cid, time.time()
        self.directory = root / "subtitles" / bvid / uuid4().hex if root else None
        self.secrets = redaction_values(credentials)

    def safe_text(self, text: str) -> str:
        return scrub_text(text, self.secrets)

    def begin(self, body: dict[str, JsonValue], *, subtitle_id: str = "0") -> None:
        if self.directory:
            serialized = encode(body)
            freeze(
                self.directory / "intent.json",
                encode(
                    {
                        "bvid": self.bvid,
                        "cid": self.cid,
                        "language": "zh",
                        "subtitle_id": subtitle_id,
                        "wire_contract": "official_editor_origin_v001",
                        "origin_subtitle_id": subtitle_id if subtitle_id != "0" else None,
                        "started_at": self.started,
                        "subtitle_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
                    }
                ),
            )
            freeze(self.directory / "subtitle.json", serialized)

    def response(self, action: str, response: httpx.Response) -> None:
        if self.directory:
            save(
                self.directory / f"{action}.json",
                encode(
                    {
                        "http_status": response.status_code,
                        "elapsed_seconds": time.time() - self.started,
                        "body": scrub_text(response.text, self.secrets)[:1000000],
                    }
                ),
            )

    def failure(self, error: Exception) -> None:
        if self.directory:
            save(
                self.directory / "failure.json",
                encode(
                    {
                        "exception_type": type(error).__name__,
                        "write_result_unknown": True,
                    }
                ),
            )
