import http.cookiejar
import shutil
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path

from pydantic import BaseModel
from yt_dlp import YoutubeDL

from youmo_workflow.adapters.download_diagnostics import DownloadLog
from youmo_workflow.adapters.download_retry import download_with_retry
from youmo_workflow.adapters.media import probe
from youmo_workflow.adapters.production_files import digest, save
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.media_limits import MAX_SOURCE_SECONDS, MIN_SOURCE_SECONDS, SourceTooLong
from youmo_workflow.domain.models import VideoDTO
from youmo_workflow.domain.source_skip import SourceDurationEvidence
from youmo_workflow.execution_limits import limited


class DownloadInfo(BaseModel):
    id: str
    duration: float


class DownloadReceipt(BaseModel):
    video_id: str
    sha256: str
    duration: float
    source_kind: str = "youtube_original"
    acquisition: str = "downloaded"


class QuietLog:
    def __init__(self, ydl: object = None) -> None:
        del ydl

    def debug(self, message: str) -> None:
        pass

    info = debug
    error = debug
    stdout = debug
    stderr = debug

    def warning(self, message: str, *, once: bool = False, only_once: bool = False) -> None:
        pass


class YoutubeAcquisition:
    def __init__(
        self,
        cookie_path: Path | None,
        proxy: str | None,
        cache_root: Path | None = None,
        session_loader: Callable[[http.cookiejar.CookieJar], None] | None = None,
    ) -> None:
        self.cookie_path, self.proxy, self.cache_root = cookie_path, proxy, cache_root
        self.session_loader = session_loader

    @limited("download")
    def acquire(self, video: VideoDTO, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / "original.mp4"
        receipt_path = directory / "download.json"
        if receipt_path.exists():
            receipt = DownloadReceipt.model_validate_json(receipt_path.read_bytes())
            if receipt.video_id != video.video_id or digest(destination) != receipt.sha256:
                raise WorkflowError("SOURCE_MEDIA_CHANGED", "缓存原片身份或哈希已改变。")
            return destination
        if self.cache_root and reuse_collector(self.cache_root, video, directory):
            return destination
        if shutil.disk_usage(directory).free < 2 * 1024**3:
            raise WorkflowError("DISK_SPACE_LOW", "下载前剩余空间不足2GB。")
        try:
            info = download_with_retry(
                directory, video.video_id, lambda: self.extract(video, directory)
            )
        except WorkflowError:
            raise
        except Exception as error:
            code = download_error_code(str(error))
            raise WorkflowError(
                code,
                "原片受地区限制，当前网络无法下载。"
                if code == "SOURCE_REGION_RESTRICTED"
                else "原片下载不可用，可能需要登录/验证；未用参考成片替代。",
            ) from None
        try:
            validate_download_identity(video.video_id, info, destination)
        except SourceTooLong as error:
            save(
                directory / "source-duration-rejection.json",
                SourceDurationEvidence(
                    video_id=error.video_id, duration_seconds=error.duration
                ).model_dump_json(),
            )
            raise
        metadata = probe(destination)
        if not metadata.has_audio or abs(info.duration - metadata.duration_seconds) > 3:
            raise WorkflowError("SOURCE_MEDIA_MISMATCH", "原片音轨或时长不完整。")
        save(
            receipt_path,
            DownloadReceipt(
                video_id=video.video_id,
                sha256=digest(destination),
                duration=metadata.duration_seconds,
            ).model_dump_json(),
        )
        return destination

    def extract(self, video: VideoDTO, directory: Path) -> DownloadInfo:
        with make_downloader(directory, self.proxy) as downloader:
            if self.session_loader:
                self.session_loader(downloader.cookiejar)
            else:
                load_cookie_jar(downloader.cookiejar, self.cookie_path)
            return DownloadInfo.model_validate(
                downloader.extract_info(
                    f"https://www.youtube.com/watch?v={video.video_id}", download=True
                )
            )


def validate_download_identity(video_id: str, info: DownloadInfo, destination: Path) -> None:
    if info.id != video_id:
        raise WorkflowError("SOURCE_MEDIA_MISMATCH", "下载原片身份不匹配。")
    if info.duration > MAX_SOURCE_SECONDS:
        raise SourceTooLong(video_id, info.duration)
    if not MIN_SOURCE_SECONDS <= info.duration <= MAX_SOURCE_SECONDS:
        raise WorkflowError("SOURCE_DURATION_UNSUPPORTED", "原片时长超出当前20–1800秒的处理范围。")
    if not destination.exists():
        raise WorkflowError("SOURCE_MEDIA_MISMATCH", "下载没有产生匹配的完整原片。")


def load_cookie_jar(target: http.cookiejar.CookieJar, path: Path | None) -> None:
    if path is None or not path.is_file():
        return
    source = http.cookiejar.MozillaCookieJar(str(path))
    source.load(ignore_discard=True, ignore_expires=False)
    for cookie in source:
        if cookie.domain.lstrip(".") in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            target.set_cookie(cookie)


def reuse_collector(root: Path, video: VideoDTO, directory: Path) -> bool:
    database = root / "data/collector.sqlite3"
    if not database.is_file():
        return False
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT channel_id,file_path,file_size FROM videos WHERE id=? AND status='completed'",
            (video.video_id,),
        ).fetchone()
    if not row or row[0] != video.channel_id or not isinstance(row[1], str):
        return False
    source = Path(row[1]).resolve()
    expected = (root / "downloads" / row[0] / f"{video.video_id}.mp4").resolve()
    if source != expected or not source.is_file() or source.stat().st_size != row[2]:
        return False
    info = probe(source)
    if not info.has_audio or not MIN_SOURCE_SECONDS <= info.duration_seconds <= MAX_SOURCE_SECONDS:
        return False
    sha = digest(source)
    destination = directory / "original.mp4"
    shutil.copyfile(source, destination)
    if digest(destination) != sha:
        raise WorkflowError("SOURCE_COPY_FAILED", "原片复用校验失败。")
    save(
        directory / "download.json",
        DownloadReceipt(
            video_id=video.video_id,
            sha256=sha,
            duration=info.duration_seconds,
            acquisition="reused_collector_original",
        ).model_dump_json(),
    )
    return True


def admission(info: Mapping[str, object], incomplete: bool = False) -> str | None:
    del incomplete
    duration = info.get("duration")
    if info.get("is_live") or info.get("live_status") == "is_upcoming":
        return "Live sources are not eligible"
    if (
        isinstance(duration, (int, float))
        and not MIN_SOURCE_SECONDS <= duration <= MAX_SOURCE_SECONDS
    ):
        return "Source duration is outside the 20-1800 second production limit"
    return None


def download_error_code(message: str) -> str:
    lowered = message.lower()
    if any(
        phrase in lowered
        for phrase in (
            "the uploader has not made this video available in your country",
            "this video is not available in your country",
        )
    ):
        return "SOURCE_REGION_RESTRICTED"
    if any(
        word in lowered
        for word in ("sign in", "not a bot", "login required", "confirm your age", "captcha")
    ):
        return "SOURCE_LOGIN_REQUIRED"
    if "cookie" in lowered:
        return "SOURCE_SESSION_INVALID"
    return "SOURCE_DOWNLOAD_UNAVAILABLE"


def make_downloader(directory: Path, proxy: str | None) -> YoutubeDL:
    node = shutil.which("node")
    if node is None:
        raise WorkflowError("NODE_MISSING", "原片下载需要本地Node运行时。")
    return YoutubeDL(
        {
            "outtmpl": str(directory / "original.%(ext)s"),
            "format": "bv[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "allowed_extractors": ["youtube$"],
            "js_runtimes": {"node": {"path": node}},
            "proxy": proxy or "",
            "ffmpeg_location": shutil.which("ffmpeg"),
            "quiet": True,
            "noprogress": True,
            "logger": DownloadLog(directory=directory),
            "overwrites": False,
            "continuedl": True,
            "skip_unavailable_fragments": False,
            "retries": 0,
            "fragment_retries": 0,
            "extractor_retries": 0,
            "socket_timeout": 20,
            "max_filesize": 250 * 1024**2,
            "windowsfilenames": True,
            "match_filter": admission,
        }
    )
