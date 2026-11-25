"""Allowlisted extractor signals only: never persist cookies, URLs or player payloads."""

import re
from importlib.metadata import version
from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue

from youmo_workflow.adapters.production_files import encode, save
from youmo_workflow.domain.time import now_utc, stamp

SIGNALS = {
    "player_js_incompatible": r"addOnDisposeCallback|n challenge solving failed|error solving .*challenge",
    "javascript_runtime_missing": r"no supported javascript runtime|no challenge solver",
    "format_unavailable": r"requested format.*not available|only images.*available|no video formats",
    "tls_error": r"\bTLS\b|\bSSL\b|handshake|UNEXPECTED_EOF_WHILE_READING",
    "connection_error": r"unable to connect|connection reset|remote end closed|timed out|timeout",
    "login_required": r"sign in|not a bot|login required|confirm your age|captcha",
    "session_error": r"cookie.*(?:expired|invalid|no longer valid)",
    "region_restricted": r"not available in your country|not made this video available in your country",
    "media_url_missing": r"missing a URL|SABR-only",
    "po_token_required": r"PO Token.*(?:required|not provided)|require.*PO Token",
}


class DownloadLog:
    def __init__(self, ydl: object = None, *, directory: Path | None = None) -> None:
        del ydl
        self.path = (
            directory / "download-diagnostics" / f"{uuid4().hex}.json" if directory else None
        )
        self.events: list[JsonValue] = []

    def debug(self, message: str) -> None:
        pass  # Debug can contain private player/session data, so it is never journaled.

    info = debug
    stdout = debug
    stderr = debug

    def warning(self, message: str, *, once: bool = False, only_once: bool = False) -> None:
        self.record("warning", message)

    def error(self, message: str) -> None:
        self.record("error", message)

    def record(self, level: str, message: str) -> None:
        if self.path is None or len(self.events) >= 50:
            return
        signals: list[JsonValue] = [
            name for name, pattern in SIGNALS.items() if re.search(pattern, message, re.I)
        ]
        status = re.search(r"\bHTTP(?: Error)?\s+([45][0-9]{2})\b", message, re.I)
        self.events.append(
            {
                "at": stamp(now_utc()),
                "level": level,
                "signals": signals or ["unclassified"],
                "http_status": int(status[1]) if status else None,
            }
        )
        save(self.path, encode({"yt_dlp_version": version("yt-dlp"), "events": self.events}))
