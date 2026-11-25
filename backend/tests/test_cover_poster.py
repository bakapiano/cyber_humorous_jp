import pytest
from PIL import Image, ImageChops, ImageEnhance
from production_support import WORKSPACE
from pydantic import ValidationError

from youmo_workflow.adapters.cover_poster import check_poster_variant, paint_poster, text_runs
from youmo_workflow.adapters.packaging_models import CoverVariant
from youmo_workflow.adapters.production_profile import ProductionProfile
from youmo_workflow.domain.errors import WorkflowError


def variant(lines: list[str], words: list[str]) -> CoverVariant:
    return CoverVariant(
        variant_id="A",
        title_id="TITLE1",
        phrase_id="P1",
        lines=lines,
        anchor="center_middle",
        red_keywords=words,
    )


@pytest.mark.parametrize(
    "lines,words",
    [
        (["这次真的涨价了"], ["涨价"]),
        (["同一份便当", "价格又涨了"], ["又涨"]),
        (["看清这份账单", "收费多了一项", "到底谁来买单"], ["多了", "谁来"]),
        (["便利店便当涨价", "一份要卖500日元", "消费者怎么看呢"], ["涨价", "500"]),
    ],
)
def test_poster_renders_one_to_three_lines_without_overflow(
    lines: list[str], words: list[str]
) -> None:
    settings = ProductionProfile(WORKSPACE).renderer(poster=True)
    base = Image.new("RGB", settings.canvas, (90, 110, 125))
    result = paint_poster(base, variant(lines, words), settings)
    background = ImageEnhance.Brightness(base).enhance(settings.background_brightness)
    bounds = ImageChops.difference(result, background).getbbox()
    assert bounds and bounds[0] >= settings.side_margin and bounds[2] <= 1280 - settings.side_margin
    assert bounds[1] >= settings.top and bounds[3] <= settings.top + settings.max_text_height
    assert bounds[2] - bounds[0] >= 800
    colours = result.getcolors(1280 * 720)
    assert colours is not None
    counts = {colour: count for count, colour in colours}
    assert counts.get((255, 230, 0), 0) > 500
    assert counts.get((255, 59, 48), 0) > 500
    assert counts.get((17, 17, 17), 0) > 500
    assert result.size == (1280, 720) and base.getpixel((0, 0)) == (90, 110, 125)


@pytest.mark.parametrize(
    "lines,words",
    [
        (["测试封面"], []),
        (["测试封面"], ["不存在"]),
        (["测试封面"], ["测试封面"]),
        (["测试", "封面排版"], ["测试"]),
        (["测试封面"], ["测试", "测试"]),
        (["测试封面"], ["测试", "测试封"]),
        (["测试", "封面"], ["试封"]),
        (["测试 封面"], ["测试"]),
        (["一二三四五六七八九十多"], ["一二"]),
        ([""], ["测试"]),
    ],
)
def test_invalid_highlights_or_layout_fail_without_rewriting(
    lines: list[str], words: list[str]
) -> None:
    value = variant(lines, words)
    before = value.model_dump_json()
    with pytest.raises(WorkflowError):
        check_poster_variant(value)
    assert value.model_dump_json() == before


def test_four_lines_are_rejected_by_schema() -> None:
    with pytest.raises(ValidationError):
        variant(["一行", "二行", "三行", "四行"], ["一"])


def test_runs_preserve_punctuation_digits_and_repeated_highlights() -> None:
    text = "500日元？500日元！"
    runs = text_runs(text, ["500"])
    assert "".join(part for part, _ in runs) == text
    assert [part for part, marked in runs if marked] == ["500", "500"]


def test_profile_preserves_frozen_poster_vision_and_renderer() -> None:
    profile = ProductionProfile(WORKSPACE)
    plan, vision = profile.cover_prompt({}), profile.vision_prompt({})
    assert "1–3行" in plan and "黄色" in plan and "红色" in plan
    assert "red_keywords" in vision and "center_middle" in vision
    assert "{{input_json}}" not in plan + vision
    assert profile.renderer(poster=True).layout == "poster"
    assert profile.renderer().layout == "legacy"
    assert "publication_titles" in plan and "逐字" in plan
    assert '"hook"' not in profile.title_prompt({})
