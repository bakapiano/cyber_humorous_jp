"""Deterministic poster typography: model-owned text and exact highlight spans."""

import re
from math import ceil, floor

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from youmo_workflow.adapters.packaging_models import CoverVariant, RenderSettings
from youmo_workflow.domain.errors import WorkflowError


def text_runs(line: str, keywords: list[str]) -> list[tuple[str, bool]]:
    if not keywords:
        return [(line, False)]
    pattern = (
        "(" + "|".join(re.escape(word) for word in sorted(keywords, key=len, reverse=True)) + ")"
    )
    return [(part, part in keywords) for part in re.split(pattern, line) if part]


def check_poster_variant(variant: CoverVariant) -> None:
    lines, words = variant.lines, variant.red_keywords
    valid_lines = all(1 <= len(line) <= 10 and not any(c.isspace() for c in line) for line in lines)
    valid_words = 1 <= len(words) <= 3 and len(set(words)) == len(words)
    if not valid_lines or not valid_words or any(not 1 <= len(word) <= 8 for word in words):
        raise WorkflowError(
            "PRODUCTION_VALIDATION_FAILED", "大字报需1–3行、每行1–10字及1–3个不重复的标红词。"
        )
    runs = [run for line in lines for run in text_runs(line, words)]
    marked = {text for text, highlighted in runs if highlighted}
    red_count = sum(len(text) for text, highlighted in runs if highlighted)
    red_line = any(all(red for _, red in text_runs(line, words)) for line in lines)
    if marked != set(words) or not 0 < red_count * 2 <= sum(map(len, lines)) <= 24 or red_line:
        raise WorkflowError(
            "PRODUCTION_VALIDATION_FAILED",
            "标红词必须完整出现在单行原文中，且红字不超过总字数一半。",
        )
    if variant.anchor != "center_middle":
        raise WorkflowError("PRODUCTION_VALIDATION_FAILED", "大字报使用居中大字布局。")


def text_box(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, stroke: int
) -> tuple[int, int, int, int]:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return floor(box[0]), floor(box[1]), ceil(box[2]), ceil(box[3])


def fitted_font(
    draw: ImageDraw.ImageDraw, lines: list[str], settings: RenderSettings
) -> tuple[ImageFont.FreeTypeFont, list[tuple[int, int, int, int]]]:
    for size in range(settings.max_font_size, settings.min_font_size - 1, -2):
        font = ImageFont.truetype(settings.font_path, size)
        boxes = [text_box(draw, line, font, settings.stroke_width) for line in lines]
        width = max(box[2] - box[0] for box in boxes)
        height = sum(box[3] - box[1] for box in boxes) + settings.line_spacing * (len(lines) - 1)
        if width <= settings.max_text_width and height <= settings.max_text_height:
            return font, boxes
    raise WorkflowError("COVER_TEXT_OVERFLOW", "大字报文字无法在最小字号下放入安全区域。")


def line_ink(
    text: str, keywords: list[str], font: ImageFont.FreeTypeFont, settings: RenderSettings
) -> Image.Image:
    # Colour the fill through a full-line mask. Stroke is drawn once, so a red
    # run cannot overwrite the neighbouring yellow glyph's outline.
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    box = text_box(probe, text, font, settings.stroke_width)
    ink = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]))
    origin = (-box[0], -box[1])
    ImageDraw.Draw(ink).text(
        origin,
        text,
        font=font,
        fill=settings.stroke,
        stroke_width=settings.stroke_width,
        stroke_fill=settings.stroke,
    )
    mask = Image.new("L", ink.size)
    ImageDraw.Draw(mask).text(origin, text, font=font, fill=255)
    colours = Image.new("RGBA", ink.size, settings.fill)
    paint = ImageDraw.Draw(colours)
    prefix = ""
    for part, highlighted in text_runs(text, keywords):
        left = origin[0] + probe.textlength(prefix, font=font)
        prefix += part
        right = origin[0] + probe.textlength(prefix, font=font)
        if highlighted:
            paint.rectangle(
                (round(left), 0, round(right) - 1, ink.height), fill=settings.highlight_fill
            )
    ink.paste(colours, (0, 0), mask)
    return ink


def paint_poster(base: Image.Image, variant: CoverVariant, settings: RenderSettings) -> Image.Image:
    check_poster_variant(variant)
    image = ImageEnhance.Brightness(base.convert("RGB")).enhance(settings.background_brightness)
    font, boxes = fitted_font(ImageDraw.Draw(image), variant.lines, settings)
    height = sum(box[3] - box[1] for box in boxes) + settings.line_spacing * (len(boxes) - 1)
    top = settings.top + (settings.max_text_height - height) // 2
    for line in variant.lines:
        ink = line_ink(line, variant.red_keywords, font, settings)
        left = (image.width - ink.width) // 2
        if left < settings.side_margin or top < 0 or top + ink.height > image.height:
            raise WorkflowError("COVER_TEXT_OVERFLOW", "大字报文字超出画布安全区域。")
        image.paste(ink, (left, top), ink)
        top += ink.height + settings.line_spacing
    return image
