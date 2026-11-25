import unicodedata
from pathlib import Path

import pytest

from youmo_workflow.adapters.subtitle_review import load_rules
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.subtitle_filter import DenyRule, SubtitleRules, WordFilter


def configured_filter() -> WordFilter:
    rules, _ = load_rules(Path(__file__).parents[2] / "config/subtitle-denylist.json")
    return WordFilter(rules)


@pytest.mark.parametrize(
    "text",
    [
        "习近平",
        "習近平",
        "习 主席",
        "習\u200b近平",
        "Ｘｉ Ｊｉｎｐｉｎｇ",
        "シー・ジンピン",
        "中国共产党",
        "中國共產黨",
        "中国共産党",
        "中共",
        "CCP",
        "Chinese Communist Party",
        "法轮功",
        "法輪功",
        "Falun Gong",
        "falun dafa",
        "ファルンゴン",
    ],
)
def test_aliases_are_masked_character_for_character(text: str) -> None:
    texts, counts = configured_filter().clean([text])
    expected = "".join(
        character if character.isspace() or unicodedata.category(character) == "Cf" else "*"
        for character in text
    )
    assert texts == [expected] and sum(counts.values()) == 1
    assert len(texts[0]) == len(text)


def test_split_words_and_english_boundaries() -> None:
    words = configured_filter()
    assert words.clean(["新闻习", "近平表示"])[0] == ["新闻*", "**表示"]
    assert words.clean(["ACCPX", "CCP_thing", "CCP news"])[0] == ["ACCPX", "CCP_thing", "*** news"]
    assert words.clean_cues(["中文\n習", "报道\n近平発言"])[0] == ["中文\n*", "报道\n**発言"]
    assert words.clean(["普通日本新闻"])[0] == ["普通日本新闻"]


def test_longest_match_disabled_and_new_matches() -> None:
    words = WordFilter(
        SubtitleRules(
            version="test",
            rules=[
                DenyRule(id="long", aliases=["法轮", "法轮功"]),
                DenyRule(id="nested", aliases=["习近平"]),
                DenyRule(id="off", aliases=["新闻"], enabled=False),
            ],
        )
    )
    assert words.clean(["习法轮功近平 新闻"])[0] == ["习***近平 新闻"]
    assert words.clean(["习近平 中国共产党 法輪功"])[0] == ["*** 中国共产党 法輪功"]


def test_masks_are_idempotent_and_reserved_in_rules() -> None:
    words = configured_filter()
    once, _ = words.clean(["习近平、中国共产党、法輪功"])
    assert once == ["***、*****、***"]
    assert words.clean(once) == (once, {})
    with pytest.raises(WorkflowError, match="遮罩符"):
        WordFilter(SubtitleRules(version="invalid", rules=[DenyRule(id="bad", aliases=["**"])]))


def test_invalid_or_missing_policy_fails_closed(tmp_path: Path) -> None:
    policy = tmp_path / "rules.json"
    with pytest.raises(WorkflowError, match="缺失或格式无效"):
        load_rules(policy)
    policy.write_text('{"rules":[]}', encoding="utf-8")
    with pytest.raises(WorkflowError, match="缺失或格式无效"):
        load_rules(policy)
