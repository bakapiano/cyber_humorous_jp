import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pysubs2
from pydantic import BaseModel

from youmo_workflow.adapters.editing_transition import transition_filter
from youmo_workflow.adapters.hard_subtitles import BurnLayout, prepare_burn
from youmo_workflow.adapters.media import check_subtitles, run_media
from youmo_workflow.adapters.packaging_validation import require
from youmo_workflow.adapters.production_files import digest, encode, save
from youmo_workflow.domain.editing import TimelinePart
from youmo_workflow.domain.editing_presentation import TRANSITION_SECONDS
from youmo_workflow.execution_limits import limited


class StreamTiming(BaseModel):
    codec_type: str
    duration: str


class Timings(BaseModel):
    streams: list[StreamTiming]


class RenderReceipt(BaseModel):
    sha256: str
    source_sha256: str
    seconds: float
    render_strategy: str = "legacy_single_input"
    timeline_sha256: str = ""
    burn_layout: BurnLayout | None = None
    transition_seconds: float = 0.0


@limited("render")
def render_edit(
    media: Path,
    timeline: list[TimelinePart],
    directory: Path,
    subtitle: Path | None = None,
    *,
    transition_seconds: float = 0.0,
) -> Path:
    require(transition_seconds in {0, TRANSITION_SECONDS}, "未支持的转场时长。")
    target, receipt_path = directory / "final.mp4", directory / "render.json"
    layout = prepare_burn(subtitle, media, directory) if subtitle else None
    timeline_hash = hashlib.sha256(
        encode([part.model_dump(mode="json") for part in timeline]).encode()
    ).hexdigest()
    if receipt_path.exists():
        receipt = RenderReceipt.model_validate_json(receipt_path.read_bytes())
        require(
            receipt.sha256 == digest(target) and receipt.source_sha256 == digest(media),
            "已渲染成片或原片哈希发生变化。",
        )
        require(receipt.burn_layout == layout, "渲染缓存的字幕/样式与当前请求不同。")
        require(receipt.transition_seconds == transition_seconds, "渲染缓存的转场参数不同。")
        require(
            not receipt.timeline_sha256 or receipt.timeline_sha256 == timeline_hash,
            "渲染时间轴已改变。",
        )
        return target
    temporary = directory / f"render-{uuid4().hex}.mp4"
    run_media(
        [
            "ffmpeg",
            "-v",
            "error",
            "-n",
            "-filter_buffered_frames",
            "512",
            *clip_inputs(media, timeline),
            "-filter_complex_threads",
            "2",
            "-filter_complex",
            transition_filter(timeline, burn=layout is not None)
            if transition_seconds
            else make_filter(timeline, burn=layout is not None),
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "21",
            "-threads",
            "4",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(temporary),
        ],
        directory,
        timeout=3600,
    )
    expected = timeline[-1].output_end
    validate_render(temporary, expected)
    temporary.replace(target)
    save(
        receipt_path,
        RenderReceipt(
            sha256=digest(target),
            source_sha256=digest(media),
            seconds=expected,
            render_strategy=(
                "independent_seek_transition_v005"
                if transition_seconds
                else "independent_seek_hardsub_v004"
                if layout
                else "independent_seek_v002"
            ),
            timeline_sha256=timeline_hash,
            burn_layout=layout,
            transition_seconds=transition_seconds,
        ).model_dump_json(),
    )
    return target


def clip_inputs(media: Path, timeline: list[TimelinePart]) -> list[str]:
    arguments: list[str] = []
    for part in timeline:
        arguments.extend(
            [
                "-ss",
                f"{part.source_start:.6f}",
                "-t",
                f"{part.source_end - part.source_start:.6f}",
                "-threads",
                "2",
                "-i",
                str(media),
            ]
        )
    return arguments


def make_filter(timeline: list[TimelinePart], *, burn: bool = False) -> str:
    filters, labels = list[str](), list[str]()
    for index, part in enumerate(timeline):
        length = f"{part.source_end - part.source_start:.6f}"
        filters.extend(
            [
                f"[{index}:v:0]trim=start=0:end={length},setpts=PTS-STARTPTS,"
                f"fps=30,tpad=stop_mode=clone:stop_duration=0.25,trim=duration={length},"
                f"setsar=1,format=yuv420p[v{index}]",
                f"[{index}:a:0]atrim=start=0:end={length},asetpts=PTS-STARTPTS,"
                f"aresample=48000,apad=whole_dur={length},atrim=duration={length},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]",
            ]
        )
        labels.append(f"[v{index}][a{index}]")
    video_label = "joinedv" if burn else "outv"
    filters.append("".join(labels) + f"concat=n={len(timeline)}:v=1:a=1[{video_label}][outa]")
    if burn:
        filters.append("[joinedv]ass=filename=burn-in.ass:fontsdir=subtitle-fonts[outv]")
    return ";".join(filters)


def validate_render(path: Path, expected: float) -> None:
    output = run_media(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,duration",
            "-of",
            "json",
            str(path),
        ],
        path.parent,
    )
    streams = Timings.model_validate_json(output).streams
    lengths = {row.codec_type: float(row.duration) for row in streams}
    require({"video", "audio"} <= lengths.keys(), "成片必须同时包含音视频。")
    require(abs(lengths["video"] - expected) < 0.16, "成片时长与计划不符。")
    require(abs(lengths["video"] - lengths["audio"]) < 0.12, "成片音画时长差超限。")
    run_media(
        [
            "ffmpeg",
            "-v",
            "error",
            "-xerror",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            os.devnull,
        ],
        path.parent,
        timeout=900,
    )


def remap_subtitles(source: Path, timeline: list[TimelinePart], target: Path) -> None:
    original = pysubs2.load(str(source), encoding="utf-8")
    result = pysubs2.SSAFile()
    for part in timeline:
        start, end = round(part.source_start * 1000), round(part.source_end * 1000)
        offset = round(part.output_start * 1000)
        for event in original.events:
            left, right = max(event.start, start), min(event.end, end)
            if left < right:
                result.events.append(
                    pysubs2.SSAEvent(
                        start=left - start + offset,
                        end=right - start + offset,
                        text=event.text,
                    )
                )
    result.save(str(target), encoding="utf-8")
    check_subtitles(target, timeline[-1].output_end)
