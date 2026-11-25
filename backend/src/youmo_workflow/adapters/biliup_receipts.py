import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, unquote
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter

from youmo_workflow.adapters.production_files import encode, save
from youmo_workflow.domain.publication import PublicationManifest


def credential_values(value: JsonValue) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            sensitive = any(
                part in key.lower() for part in ("token", "secret", "csrf", "sessdata", "bili_jct")
            )
            cookie = key == "value" and (
                isinstance(item, str)
                and (len(item) >= 8 or value.get("name") in {"SESSDATA", "bili_jct", "sid"})
            )
            if isinstance(item, str) and item and (sensitive or cookie):
                found.add(item)
            found.update(credential_values(item))
    elif isinstance(value, list):
        for item in value:
            found.update(credential_values(item))
    return found


def redaction_values(credentials: Path) -> set[str]:
    values = credential_values(TypeAdapter(JsonValue).validate_json(credentials.read_bytes()))
    values.update({str(credentials), credentials.as_posix()})
    return {
        variant
        for value in values
        for variant in (value, quote(value, safe=""), unquote(value), json.dumps(value)[1:-1])
    }


def scrub_text(text: str, secrets: set[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"\bvoucher_[A-Za-z0-9-]+", "[redacted-voucher]", text)
    return re.sub(
        r"(?im)((?:[\w-]*(?:token|csrf|sessdata|bili_jct|cookie|secret)[\w-]*|authorization|auth(?:_?key)?)[\"']?\s*[:=]\s*)[^\r\n]*",
        r"\1[redacted]",
        text,
    )


class ReceiptJournal:
    def __init__(self, root: Path | None, credentials: Path, manifest: PublicationManifest) -> None:
        self.started = time.time()
        self.directory: Path | None = None
        self.secrets: set[str] = set()
        if root is not None:
            self.secrets = redaction_values(credentials)
            identity = hashlib.sha256(manifest.model_dump_json().encode()).hexdigest()
            self.directory = root / identity / uuid4().hex
            save(
                self.directory / "intent.json",
                encode(
                    {
                        "content_id": manifest.content_id,
                        "title": manifest.title,
                        "source_url": manifest.source_url,
                        "visibility": manifest.visibility,
                        "started_at": self.started,
                        "manifest_sha256": identity,
                    }
                ),
            )

    def scrub(self, text: str) -> str:
        return scrub_text(text, self.secrets)

    def record(self, action: str, result: subprocess.CompletedProcess[str]) -> None:
        if self.directory is None:
            return
        output = result.stdout + result.stderr
        record: dict[str, JsonValue] = {
            "action": action,
            "return_code": result.returncode,
            "elapsed_seconds": round(time.time() - self.started, 3),
            "bvids": sorted(set(re.findall(r"BV[A-Za-z0-9]{10}", output))),
            "stdout_bytes": len(result.stdout.encode()),
            "stderr_bytes": len(result.stderr.encode()),
        }
        if action == "upload" or result.returncode:
            record.update(
                {
                    "stdout": self.scrub(result.stdout)[:1000000],
                    "stderr": self.scrub(result.stderr)[:1000000],
                }
            )
        save(self.directory / f"{action}.json", encode(record))

    def failure(self, action: str, error: Exception) -> None:
        if self.directory is not None:
            if isinstance(error, subprocess.TimeoutExpired):
                self.record(
                    action,
                    subprocess.CompletedProcess(
                        [], -1, partial_text(error.stdout), partial_text(error.stderr)
                    ),
                )
            save(
                self.directory / f"{action}-failure.json",
                encode(
                    {
                        "exception_type": type(error).__name__,
                        "elapsed_seconds": round(time.time() - self.started, 3),
                        "write_result_unknown": True,
                    }
                ),
            )


def partial_text(value: bytes | str | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
