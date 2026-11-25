import re

import pytest
from PIL import ImageFont

from youmo_workflow.adapters.hard_subtitles import (
    Dimensions,
    bilingual_text,
    font_limits,
    subtitle_font,
)
from youmo_workflow.domain.errors import WorkflowError


@pytest.mark.parametrize("size", [(360, 640), (720, 1280), (1080, 1920)])
def test_portrait_has_readable_minimum_and_preserves_long_text(size: tuple[int, int]) -> None:
    dimensions = Dimensions(width=size[0], height=size[1])
    zh = "这是一段用于检查竖屏字幕字号和自动换行的长句，所有文字必须完整保留。"
    ja = "これは縦型動画で字幕の文字サイズと改行を確認するための文章です。すべての文字を残します。"
    font = subtitle_font()
    output = bilingual_text(zh + "\n" + ja, font, dimensions)
    sizes = [int(value) for value in re.findall(r"\\fs(\d+)", output)]
    assert sizes[0] >= round(dimensions.width / 18)
    assert sizes[1] >= round(dimensions.width / 23)
    plain = re.sub(r"\{[^}]*\}", "", output)
    assert plain.replace(r"\N", "") == zh + ja
    lines = plain.split(r"\N")
    assert len(lines) <= 8
    assert all(
        ImageFont.truetype(str(font), sizes[-1]).getlength(line) <= dimensions.width * 0.94
        for line in lines
    )


def test_landscape_and_portrait_have_larger_font_limits() -> None:
    assert font_limits(Dimensions(width=1280, height=720)) == (48, 40, 3)
    assert font_limits(Dimensions(width=360, height=640)) == (24, 20, 4)


def test_portrait_overflow_never_shrinks_below_readability_floor() -> None:
    with pytest.raises(WorkflowError) as error:
        bilingual_text(
            "长" * 300 + "\n" + "長" * 300, subtitle_font(), Dimensions(width=360, height=640)
        )
    assert error.value.code == "HARDSUB_TEXT_OVERFLOW"
