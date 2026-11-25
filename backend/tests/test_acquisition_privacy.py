import http.cookiejar
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from conftest import Clock, sample_video

from youmo_workflow.adapters.acquisition import (
    YoutubeAcquisition,
    admission,
    load_cookie_jar,
    make_downloader,
)
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import VideoDTO


def test_imported_session_is_youtube_only_and_never_saved(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-session.txt"
    payload = (
        "# Netscape HTTP Cookie File\n"
        ".youtube.com\tTRUE\t/\tTRUE\t4102444800\tSYNTHETIC\tyt-test\n"
        ".example.com\tTRUE\t/\tTRUE\t4102444800\tUNRELATED\tother-test\n"
    )
    source.write_text(payload, encoding="utf-8")
    target = http.cookiejar.CookieJar()
    load_cookie_jar(target, source)
    assert [(cookie.domain, cookie.name) for cookie in target] == [(".youtube.com", "SYNTHETIC")]
    with make_downloader(tmp_path, None) as downloader:
        load_cookie_jar(downloader.cookiejar, source)
        assert downloader.params.get("cookiefile") is None
        assert downloader.params.get("cookiesfrombrowser") is None
    assert source.read_text(encoding="utf-8") == payload


def test_download_admission_rejects_live_and_outside_duration_limits() -> None:
    assert admission({"is_live": True}) is not None
    assert admission({"duration": 1801}) is not None
    assert admission({"duration": 1106}) is None
    assert admission({"duration": 10}) is not None
    assert admission({"duration": 80}) is None


class SkippedDownload:
    cookiejar = http.cookiejar.CookieJar()

    def extract_info(self, url: str, *, download: bool) -> dict[str, str | int]:
        return {"id": "abcdefghijk", "duration": 1801}


def test_unsupported_duration_is_not_reported_as_missing_media(
    tmp_path: Path, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def skipped(_directory: Path, _proxy: str | None) -> Generator[SkippedDownload]:
        yield SkippedDownload()

    monkeypatch.setattr("youmo_workflow.adapters.acquisition.make_downloader", skipped)
    video = VideoDTO(
        **sample_video(clock).model_dump(),
        source_id="tbsnewsdig",
        discovered_at="2026-11-06T03:00:00Z",
        revision=1,
        screened=False,
    )
    with pytest.raises(WorkflowError) as error:
        YoutubeAcquisition(None, None).acquire(video, tmp_path)
    assert error.value.code == "SOURCE_TOO_LONG"
    assert (tmp_path / "source-duration-rejection.json").is_file()
