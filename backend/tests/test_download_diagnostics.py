import json
from importlib.metadata import version
from pathlib import Path

import pytest

from youmo_workflow.adapters.acquisition import download_error_code, make_downloader
from youmo_workflow.adapters.download_diagnostics import DownloadLog


def test_diagnostics_store_only_safe_signals_and_keep_separate_attempts(tmp_path: Path) -> None:
    logger = DownloadLog(directory=tmp_path)
    assert logger.path is not None
    private = "secret-session-value"
    logger.debug(f"Cookie: {private}")
    assert not logger.path.exists()
    logger.warning(
        f"TypeError: a.addOnDisposeCallback is not a function {private} "
        f"https://example.test/media?signature={private}"
    )
    logger.error(f"HTTP Error 403 [SSL: UNEXPECTED_EOF_WHILE_READING] Cookie={private}")
    raw = logger.path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert private not in raw and "https" not in raw and "Cookie" not in raw
    assert data["events"][0]["signals"] == ["player_js_incompatible"]
    assert data["events"][1]["http_status"] == 403
    assert "tls_error" in data["events"][1]["signals"]
    other = DownloadLog(directory=tmp_path)
    assert other.path is not None
    other.error("unclassified error")
    assert other.path != logger.path and logger.path.read_text(encoding="utf-8") == raw
    for _ in range(60):
        other.error(private)
    assert len(json.loads(other.path.read_text(encoding="utf-8"))["events"]) == 50


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "n challenge solving failed. Confirm you are on the latest version",
            "SOURCE_DOWNLOAD_UNAVAILABLE",
        ),
        ("Sign in to confirm you are not a bot", "SOURCE_LOGIN_REQUIRED"),
        ("Please confirm your age", "SOURCE_LOGIN_REQUIRED"),
        ("captcha verification required", "SOURCE_LOGIN_REQUIRED"),
        ("cookies no longer valid", "SOURCE_SESSION_INVALID"),
        ("this video is not available in your country", "SOURCE_REGION_RESTRICTED"),
    ],
)
def test_update_advice_is_not_misclassified_as_authentication(message: str, expected: str) -> None:
    assert download_error_code(message) == expected


def test_validated_downloader_and_original_limits_are_kept(tmp_path: Path) -> None:
    assert version("yt-dlp") == "2026.11.23"
    with make_downloader(tmp_path, None) as downloader:
        assert isinstance(downloader.params.get("logger"), DownloadLog)
        assert downloader.params.get("retries") == downloader.params.get("extractor_retries") == 0
        assert downloader.params.get("fragment_retries") == 0
        assert (
            downloader.params.get("format")
            == "bv[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]"
        )
        assert "extractor_args" not in downloader.params
        assert downloader.params.get("cookiefile") is None
