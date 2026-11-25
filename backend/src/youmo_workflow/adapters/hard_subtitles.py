"""Deterministic bilingual typography; burned pixels remain bound to the reviewed SRT."""

import os
import shutil
from math import ceil
from pathlib import Path

import pysubs2
from PIL import ImageFont
from pydantic import BaseModel, Field

from youmo_workflow.adapters.media import run_media
from youmo_workflow.adapters.production_files import digest, freeze
from youmo_workflow.domain.burn_in import BurnInProof
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.quality import ArtifactReader


class Dimensions(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class VideoDimensions(BaseModel):
    streams: list[Dimensions] = Field(min_length=1, max_length=1)


class BurnLayout(BaseModel):
    subtitle_sha256: str
    ass_sha256: str
    font_sha256: str
    font_name: str
    width: int
    height: int
    cue_count: int


def subtitle_font() -> Path:
    configured = os.environ.get("WORKFLOW_SUBTITLE_FONT")
    choices = (
        [Path(configured)]
        if configured
        else [
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        ]
    )
    for path in choices:
        if path.is_file():
            return path
    raise WorkflowError("HARDSUB_FONT_MISSING", "没有中日文字体，请配置WORKFLOW_SUBTITLE_FONT。")


def literal_ass(text: str) -> str:
    # Display control punctuation literally; never execute subtitle-owned ASS tags.
    return text.translate(str.maketrans({"\\": "＼", "{": "｛", "}": "｝"}))


def wrap_line(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    paragraphs = literal_ass(text).split("\n")
    return [line for paragraph in paragraphs for line in balanced_paragraph(paragraph, font, width)]


def balanced_paragraph(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    total = font.getlength(text)
    count = max(1, ceil(total / width))
    target = min(width, total / count + font.getlength("。") * 0.4)
    lines, line = list[str](), ""
    for character in text:
        if (
            line
            and font.getlength(line + character) > target
            and character not in "。，、！？!?.,；：)]）】」』”’"
        ):
            lines.append(line)
            line = ""
        line += character
    if line:
        lines.append(line)
    return lines


def font_limits(size: Dimensions) -> tuple[int, int, int]:
    if size.height > size.width:
        maximum = max(24, round(size.width / 15))
        return maximum, max(20, round(maximum * 0.83)), 4
    maximum = max(18, round(size.width * 0.0375))
    return maximum, max(14, round(maximum * 0.83)), 3


def bilingual_text(text: str, font: Path, size: Dimensions) -> str:
    groups = text.split("\n", 1)
    if len(groups) != 2 or not all(group.strip() for group in groups):
        raise WorkflowError("BILINGUAL_SUBTITLES_REQUIRED", "烧录字幕须同时包含中文和日文两组。")
    maximum, minimum, line_limit = font_limits(size)
    for zh_size in range(maximum, minimum - 1, -1):
        ja_size = max(10, round(zh_size * 0.8))
        zh = wrap_line(groups[0], ImageFont.truetype(str(font), zh_size), int(size.width * 0.9))
        ja = wrap_line(groups[1], ImageFont.truetype(str(font), ja_size), int(size.width * 0.9))
        height = (len(zh) * zh_size + len(ja) * ja_size) * 1.3
        fits = all(
            ImageFont.truetype(str(font), point).getlength(line) <= size.width * 0.94
            for lines, point in ((zh, zh_size), (ja, ja_size))
            for line in lines
        )
        if fits and max(len(zh), len(ja)) <= line_limit and height <= size.height * 0.42:
            return (
                f"{{\\fs{zh_size}}}"
                + r"\N".join(zh)
                + f"\\N{{\\fs{ja_size}\\c&HDDDDDD&}}"
                + r"\N".join(ja)
            )
    raise WorkflowError("HARDSUB_TEXT_OVERFLOW", "双语字幕超出安全区域；不截断文字或强行缩成小字。")


def video_dimensions(media: Path) -> Dimensions:
    output = run_media(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(media),
        ],
        media.parent,
    )
    return VideoDimensions.model_validate_json(output).streams[0]


def editing_layout_version(media: Path) -> str:
    size = video_dimensions(media)
    if size.height > size.width:
        return "editing_v004_hardsub_p003"
    return "editing_v004_hardsub_l003"


def prepare_burn(subtitle: Path, media: Path, directory: Path) -> BurnLayout:
    size = video_dimensions(media)
    font = subtitle_font()
    font_name = ImageFont.truetype(str(font), 20).getname()[0]
    if not font_name:
        raise WorkflowError("HARDSUB_FONT_MISSING", "字体没有可用于烧录的字体名称。")
    source = pysubs2.load(str(subtitle), encoding="utf-8")
    result = pysubs2.SSAFile()
    result.info.update(
        {"PlayResX": str(size.width), "PlayResY": str(size.height), "WrapStyle": "2"}
    )
    result.styles["Default"] = pysubs2.SSAStyle(
        fontname=font_name,
        fontsize=36,
        primarycolor=pysubs2.Color(255, 255, 255),
        outlinecolor=pysubs2.Color(0, 0, 0, 40),
        backcolor=pysubs2.Color(0, 0, 0, 60),
        outline=max(1, size.width / 640),
        shadow=0,
        borderstyle=3,
        marginl=round(size.width * 0.05),
        marginr=round(size.width * 0.05),
        marginv=max(8, round(size.height * 0.035)),
    )
    for cue in source:
        result.events.append(
            pysubs2.SSAEvent(
                start=cue.start, end=cue.end, text=bilingual_text(cue.plaintext, font, size)
            )
        )
    target = directory / "burn-in.ass"
    freeze(target, result.to_string("ass"))
    fonts = directory / "subtitle-fonts"
    fonts.mkdir(exist_ok=True)
    copied = fonts / ("bilingual" + font.suffix)
    if not copied.exists():
        shutil.copyfile(font, copied)
    if digest(copied) != digest(font):
        raise WorkflowError("HARDSUB_FONT_CHANGED", "烧录字体发生变化，拒绝复用旧布局。")
    return BurnLayout(
        subtitle_sha256=digest(subtitle),
        ass_sha256=digest(target),
        font_sha256=digest(font),
        font_name=font_name,
        width=size.width,
        height=size.height,
        cue_count=len(source),
    )


def verify_burn_proof(assets: ArtifactReader, manifest: PublicationManifest) -> float:
    if manifest.burn_in_ref is None or manifest.subtitle_ref is None:
        raise WorkflowError("HARD_SUBTITLES_NOT_VERIFIED", "硬字幕缺少烧录回执或审查字幕。")
    proof = BurnInProof.model_validate_json(
        assets.resolve(manifest.burn_in_ref, "burn_in").read_bytes()
    )
    assets.resolve(manifest.subtitle_ref, "subtitle")
    assets.resolve(proof.ass_ref, "subtitle_ass")
    if (
        proof.media_sha256 != manifest.media_ref.sha256
        or proof.subtitle_sha256 != manifest.subtitle_ref.sha256
    ):
        raise WorkflowError(
            "HARD_SUBTITLES_NOT_VERIFIED", "烧录回执未绑定当前视频与字幕，须重新渲染。"
        )
    if not manifest.test_only and any(
        assets.describe(ref.artifact_id).test_only for ref in (manifest.burn_in_ref, proof.ass_ref)
    ):
        raise WorkflowError("TEST_ARTIFACT_DENIED", "测试烧录回执不能用于真实投稿。")
    return proof.seconds
