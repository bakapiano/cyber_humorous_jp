import shutil
import subprocess
from pathlib import Path

import pysubs2
from PIL import Image
from pydantic import BaseModel, Field

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.publication import ProbeResult


class ProbeStream(BaseModel):
    codec_type: str


class ProbeFormat(BaseModel):
    duration: str


class ProbePayload(BaseModel):
    streams: list[ProbeStream] = Field(default_factory=lambda: list[ProbeStream]())
    format: ProbeFormat


def run_media(arguments: list[str], cwd: Path, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise WorkflowError("MEDIA_PROCESS_FAILED", "媒体进程不可用或超时。") from None
    if result.returncode:
        raise WorkflowError("MEDIA_PROCESS_FAILED", "媒体处理失败；未将部分文件标为成功。")
    return result.stdout


def probe(path: Path) -> ProbeResult:
    binary = shutil.which("ffprobe")
    if not binary:
        raise WorkflowError("FFPROBE_MISSING", "未找到 FFprobe。")
    output = run_media(
        [
            binary,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        path.parent,
    )
    payload = ProbePayload.model_validate_json(output)
    duration = float(payload.format.duration)
    kinds = {stream.codec_type for stream in payload.streams}
    if "video" not in kinds or duration <= 0:
        raise WorkflowError("INVALID_MEDIA", "媒体没有有效视频流或时长。")
    return ProbeResult(duration_seconds=duration, has_video=True, has_audio="audio" in kinds)


def check_cover(path: Path) -> None:
    try:
        with Image.open(path) as image:
            if image.format not in {"JPEG", "PNG"} or min(image.size) < 100:
                raise WorkflowError("INVALID_COVER", "封面必须是有效且足够大的 JPEG / PNG。")
            image.verify()
    except OSError:
        raise WorkflowError("INVALID_COVER", "封面无法解码。") from None


def check_subtitles(path: Path, duration: float) -> None:
    try:
        subtitles = pysubs2.load(str(path), encoding="utf-8")
    except (ValueError, OSError):
        raise WorkflowError("INVALID_SUBTITLES", "字幕文件无法解析。") from None
    if not subtitles.events:
        raise WorkflowError("INVALID_SUBTITLES", "字幕为空。")
    previous = 0
    for event in subtitles.events:
        if event.start < previous or not 0 <= event.start < event.end <= duration * 1000 + 100:
            raise WorkflowError("INVALID_SUBTITLES", "字幕时间范围、顺序或重叠不符合要求。")
        if not event.plaintext.strip():
            raise WorkflowError("INVALID_SUBTITLES", "字幕包含空白内容。")
        previous = event.end
