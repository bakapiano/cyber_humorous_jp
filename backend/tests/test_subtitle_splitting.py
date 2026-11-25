import json
from itertools import pairwise
from pathlib import Path

import pysubs2
import pytest
from conftest import Assets, Clock
from production_support import prepared_content

from youmo_workflow.adapters.editing import editing_directory
from youmo_workflow.adapters.hard_subtitles import Dimensions, bilingual_text, subtitle_font
from youmo_workflow.adapters.subtitle_splitting import segment_subtitles, split_cue, split_text
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import PackagingOutput, ReadyContent
from youmo_workflow.runtime import Runtime

ZH = "医院打来电话，感觉像是让我马上过去，我看到躺在床上的丈夫，看到丈夫只是因心肺复苏而身体摇晃着的样子。"
JA = "病院から電話がかかってきて、すぐ来てくださいみたいな感じだったので、ベッドに横たわる主人がいて、夫はただその心肺蘇生で体が揺れているだけっていう姿を見て。"


def cue(text: str, start: int = 47790, end: int = 67750) -> pysubs2.SSAEvent:
    result = pysubs2.SSAEvent(start=start, end=end)
    result.plaintext = text
    return result


def test_real_overflow_preserves_bilingual_text_timing_and_punctuation_alignment() -> None:
    size, font = Dimensions(width=360, height=640), subtitle_font()
    original = cue(ZH + "\n" + JA)
    with pytest.raises(WorkflowError, match="安全区域"):
        bilingual_text(original.plaintext, font, size)
    parts = split_cue(original, font, size)
    assert len(parts) == 2
    groups = [part.plaintext.split("\n", 1) for part in parts]
    assert "".join(group[0] for group in groups) == ZH
    assert "".join(group[1] for group in groups) == JA
    assert parts[0].start == original.start and parts[-1].end == original.end
    assert all(left.end == right.start for left, right in pairwise(parts))
    assert all(part.start < part.end for part in parts)
    for part in parts:
        assert "{\\fs24}" in bilingual_text(part.plaintext, font, size)
    assert groups[0][0].count("，") == groups[0][1].count("、")


@pytest.mark.parametrize("size", [(360, 640), (1280, 720)])
def test_very_long_unpunctuated_masked_text_is_not_lost(size: tuple[int, int]) -> None:
    zh, ja = "中文***" * 80, "日本語***" * 90
    parts = split_text(zh + "\n" + ja, subtitle_font(), Dimensions(width=size[0], height=size[1]))
    assert len(parts) > 1
    assert "".join(part[0] for part in parts) == zh
    assert "".join(part[1] for part in parts) == ja
    assert all(part[0].strip() and part[1].strip() for part in parts)


def test_short_cue_is_unchanged_and_missing_language_is_not_fabricated() -> None:
    size, font = Dimensions(width=360, height=640), subtitle_font()
    original = cue("短字幕\n短い字幕")
    assert split_cue(original, font, size) == [original]
    assert split_cue(original, font, size)[0] is original
    with pytest.raises(WorkflowError, match="完整中日"):
        split_text("只有一种语言", font, size)


def test_srt_roundtrip_keeps_spaces_between_words() -> None:
    zh = "很长的中文 phrase with spaces， " * 20 + "结束。"
    ja = "長い日本語 phrase with spaces、 " * 20 + "終わり。"
    parts = split_cue(
        cue(zh + "\n" + ja, 0, 90000), subtitle_font(), Dimensions(width=360, height=640)
    )
    output = pysubs2.SSAFile()
    output.events = parts
    restored = pysubs2.SSAFile.from_string(output.to_string("srt"), format_="srt")
    groups = [part.plaintext.split("\n", 1) for part in restored.events]
    assert "".join(group[0] for group in groups) == zh
    assert "".join(group[1] for group in groups) == ja


def test_file_split_is_idempotent_and_preserves_review_source(
    runtime: Runtime, assets: Assets, tmp_path: Path
) -> None:
    source = tmp_path / "reviewed.srt"
    original = pysubs2.SSAFile()
    original.events = [cue((ZH + "，") * 4 + "\n" + (JA + "、") * 4, 0, 1900)]
    source.write_text(original.to_string("srt"), encoding="utf-8")
    before = source.read_bytes()
    media = runtime.artifacts.resolve(assets.media, "media")
    directory = tmp_path / "split"
    target = segment_subtitles(source, media, directory, 2)
    assert target != source and source.read_bytes() == before
    result = pysubs2.load(str(target)).events
    assert result[0].start == 0 and result[-1].end == 1900 and len(result) > 1
    assert segment_subtitles(source, media, directory, 2) == target
    record = json.loads((directory / "split.json").read_text(encoding="utf-8"))
    assert record["text_preserved"] and record["outer_timing_preserved"]
    assert not record["word_alignment_claimed"]


def test_finished_legacy_layout_stays_read_only(runtime: Runtime, clock: Clock) -> None:
    content = prepared_content(runtime, clock)
    ready = ReadyContent(
        content=content,
        packaging=PackagingOutput(
            content_id=content.content_id,
            title="fixture",
            cover_ref=content.original_media_ref,
            original_media_ref=content.original_media_ref,
        ),
    )
    media = runtime.artifacts.resolve(content.original_media_ref, "media")
    assert editing_directory(runtime.artifacts, ready, media).name == "editing_v004_hardsub_l004"
    old = runtime.artifacts.root / "production" / content.content_id / "editing_v004_hardsub_l003"
    old.mkdir(parents=True)
    marker = old / "output.json"
    marker.write_text("immutable old cache", encoding="utf-8")
    assert editing_directory(runtime.artifacts, ready, media) == old
    assert marker.read_text(encoding="utf-8") == "immutable old cache"
