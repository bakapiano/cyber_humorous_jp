import hashlib
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from youmo_workflow.adapters.cover_poster import paint_poster
from youmo_workflow.adapters.media import run_media
from youmo_workflow.adapters.packaging_models import (
    CoverChoice,
    CoverPlanning,
    CoverVariant,
    Frame,
    FrameIndex,
    RenderSettings,
)
from youmo_workflow.adapters.packaging_validation import require
from youmo_workflow.adapters.production_files import digest, save
from youmo_workflow.execution_limits import limited


@limited("render")
def extract_frames(
    media: Path, content_id: str, plan: CoverPlanning, directory: Path
) -> list[Frame]:
    directory.mkdir(parents=True, exist_ok=True)
    index = directory / "index.json"
    if index.exists():
        frames = FrameIndex.model_validate_json(index.read_bytes()).frames
        for frame in frames:
            require(digest(directory / f"{frame.frame_id}.jpg") == frame.sha256, "缓存帧已改变。")
        return frames
    seed = 20261106 + int(hashlib.sha256(content_id.encode()).hexdigest()[:8], 16)
    rng, frames = random.Random(seed), list[Frame]()
    for region in plan.cover_regions:
        width = (region.end_sec - region.start_sec) / 4
        for number in range(4):
            timestamp = round(region.start_sec + (number + 0.2 + rng.random() * 0.6) * width, 3)
            frame_id = f"F{len(frames) + 1:03}"
            path = directory / f"{frame_id}.jpg"
            if not path.exists():
                run_media(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-n",
                        "-ss",
                        str(timestamp),
                        "-i",
                        str(media),
                        "-frames:v",
                        "1",
                        "-q:v",
                        "2",
                        str(path),
                    ],
                    directory,
                )
            with Image.open(path) as image:
                image.verify()
            frames.append(
                Frame(
                    frame_id=frame_id,
                    region_id=region.region_id,
                    timestamp_sec=timestamp,
                    sha256=digest(path),
                )
            )
    save(index, FrameIndex(frames=frames).model_dump_json())
    return frames


def render_covers(
    choice: CoverChoice, frame: Frame, directory: Path, settings: RenderSettings
) -> None:
    source = directory / "frames" / f"{frame.frame_id}.jpg"
    require(digest(source) == frame.sha256, "封面源帧已改变。")
    with Image.open(source) as raw:
        base = Image.new("RGB", settings.canvas, "black")
        fitted = ImageOps.contain(raw.convert("RGB"), base.size, Image.Resampling.LANCZOS)
        base.paste(fitted, ((base.width - fitted.width) // 2, (base.height - fitted.height) // 2))
    base.save(directory / "selected_frame.png")
    for variant in choice.covers:
        image = paint_text(base, variant, settings)
        image.save(directory / f"cover_{variant.variant_id}.png")


def paint_text(base: Image.Image, variant: CoverVariant, settings: RenderSettings) -> Image.Image:
    if settings.layout == "poster":
        return paint_poster(base, variant, settings)
    image = base.copy()
    draw = ImageDraw.Draw(image)
    text = "\n".join(variant.lines)
    for size in range(settings.max_font_size, settings.min_font_size - 1, -2):
        font = ImageFont.truetype(settings.font_path, size)
        box = draw.multiline_textbbox(
            (0, 0),
            text,
            font=font,
            spacing=settings.line_spacing,
            stroke_width=settings.stroke_width,
            align="center",
        )
        width, height = box[2] - box[0], box[3] - box[1]
        if width <= settings.max_text_width and height <= settings.max_text_height:
            break
    else:
        raise ValueError("Cover text cannot fit the pinned layout")
    left = settings.side_margin
    if variant.anchor == "right_middle":
        left = image.width - settings.side_margin - width
    draw.multiline_text(
        (left - box[0], settings.top - box[1]),
        text,
        font=font,
        spacing=settings.line_spacing,
        align="center",
        fill=settings.fill,
        stroke_fill=settings.stroke,
        stroke_width=settings.stroke_width,
    )
    return image
