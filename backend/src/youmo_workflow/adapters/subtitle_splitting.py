"""Split overflowing reviewed bilingual cues without rewriting their text or outer timing."""

import re
import unicodedata
from pathlib import Path

import pysubs2
from pydantic import JsonValue

from youmo_workflow.adapters.hard_subtitles import (
    Dimensions,
    bilingual_text,
    subtitle_font,
    video_dimensions,
)
from youmo_workflow.adapters.media import check_subtitles
from youmo_workflow.adapters.production_files import digest, encode, freeze
from youmo_workflow.domain.errors import WorkflowError

Pair = tuple[str, str]
PUNCTUATION = re.compile(r"[，、。！？；,!?;]+[」』”’）】]*")


def safe_boundary(text: str, index: int) -> bool:
    return (
        0 < index < len(text)
        and bool(text[:index].strip())
        and bool(text[index:].strip())
        and unicodedata.category(text[index]) not in {"Mn", "Mc"}
        and text[index] != "\u200d"
        and text[index - 1] != "\u200d"
        and not 0x1F3FB <= ord(text[index]) <= 0x1F3FF
    )


def punctuation_boundaries(text: str) -> list[int]:
    return [match.end() for match in PUNCTUATION.finditer(text) if safe_boundary(text, match.end())]


def middle_boundary(text: str) -> int:
    valid = [index for index in range(1, len(text)) if safe_boundary(text, index)]
    if not valid:
        raise WorkflowError("SUBTITLE_SPLIT_UNALIGNABLE", "字幕过长但无法保留非空双语分段。")
    natural = punctuation_boundaries(text) + [index for index in valid if text[index - 1].isspace()]
    balanced = [index for index in natural if 0.25 <= index / len(text) <= 0.75]
    return min(balanced or valid, key=lambda index: abs(index - len(text) / 2))


def divide_pair(pair: Pair) -> tuple[Pair, Pair]:
    zh, ja = pair
    zh_cuts, ja_cuts = punctuation_boundaries(zh), punctuation_boundaries(ja)
    if zh_cuts and len(zh_cuts) == len(ja_cuts):
        selected = min(
            range(len(zh_cuts)),
            key=lambda index: (
                abs(zh_cuts[index] / len(zh) - 0.5) + abs(ja_cuts[index] / len(ja) - 0.5)
            ),
        )
        left, right = zh_cuts[selected], ja_cuts[selected]
    else:
        left, right = middle_boundary(zh), middle_boundary(ja)
    # SRT trims the outer cue edges: keep Chinese spaces before the language break,
    # and Japanese spaces after it, so serialization preserves every character.
    while left < len(zh) and zh[left].isspace():
        left += 1
    while right > 0 and ja[right - 1].isspace():
        right -= 1
    return (zh[:left], ja[:right]), (zh[left:], ja[right:])


def fits(pair: Pair, font: Path, size: Dimensions) -> bool:
    try:
        bilingual_text(pair[0] + "\n" + pair[1], font, size)
        return True
    except WorkflowError as error:
        if error.code != "HARDSUB_TEXT_OVERFLOW":
            raise
        return False


def split_text(text: str, font: Path, size: Dimensions) -> list[Pair]:
    groups = text.split("\n", 1)
    if len(groups) != 2 or not all(group.strip() for group in groups):
        raise WorkflowError("BILINGUAL_SUBTITLES_REQUIRED", "拆分前字幕必须包含完整中日两组。")
    original = (groups[0], groups[1])
    pending, result = [original], list[Pair]()
    while pending:
        pair = pending.pop()
        if fits(pair, font, size):
            result.append(pair)
        else:
            left, right = divide_pair(pair)
            pending.extend([right, left])
    if any("".join(pair[index] for pair in result) != original[index] for index in (0, 1)):
        raise WorkflowError("SUBTITLE_SPLIT_TEXT_CHANGED", "拆分不得删字、改写或重排原字幕。")
    return result


def split_cue(cue: pysubs2.SSAEvent, font: Path, size: Dimensions) -> list[pysubs2.SSAEvent]:
    parts = split_text(cue.plaintext, font, size)
    if len(parts) == 1:
        return [cue]
    weights = [max(1, sum(not char.isspace() for char in ja)) for _, ja in parts]
    total, consumed, start = sum(weights), 0, cue.start
    events: list[pysubs2.SSAEvent] = []
    for (zh, ja), weight in zip(parts, weights, strict=True):
        consumed += weight
        end = cue.start + round((cue.end - cue.start) * consumed / total)
        if end <= start:
            raise WorkflowError(
                "SUBTITLE_SPLIT_TIMING_INVALID", "原字幕时段太短，无法完整分配拆分时间。"
            )
        event = pysubs2.SSAEvent(start=start, end=end)
        event.plaintext = zh + "\n" + ja
        events.append(event)
        start = end
    return events


def segment_subtitles(source: Path, media: Path, directory: Path, seconds: float) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    font, size = subtitle_font(), video_dimensions(media)
    original = pysubs2.load(str(source), encoding="utf-8")
    result = pysubs2.SSAFile()
    mappings: list[JsonValue] = []
    for index, cue in enumerate(original.events, 1):
        children = split_cue(cue, font, size)
        result.events.extend(children)
        if len(children) > 1:
            mappings.append(
                {
                    "original_cue": index,
                    "start": cue.start,
                    "end": cue.end,
                    "parts": [{"start": child.start, "end": child.end} for child in children],
                }
            )
    target = directory / "segmented.zh-ja.srt" if mappings else source
    if mappings:
        freeze(target, result.to_string("srt"))
    check_subtitles(target, seconds)
    freeze(
        directory / "split.json",
        encode(
            {
                "version": "bilingual_split_v001",
                "source_sha256": digest(source),
                "output_sha256": digest(target),
                "font_sha256": digest(font),
                "width": size.width,
                "height": size.height,
                "original_cues": len(original),
                "output_cues": len(result),
                "split_cues": mappings,
                "text_preserved": True,
                "outer_timing_preserved": True,
                "timing_method": "proportional_japanese_characters",
                "word_alignment_claimed": False,
            }
        ),
    )
    return target
