import json
import shutil
from pathlib import Path

import pytest
from production_support import WORKSPACE, cover_plan, raw_titles, transcript
from pydantic import ValidationError

from youmo_workflow.adapters.packaging_models import CoverPlanning
from youmo_workflow.adapters.production_files import encode
from youmo_workflow.adapters.production_profile import ProductionProfile
from youmo_workflow.adapters.raw_titles import check_title_binding, parse_titles
from youmo_workflow.domain.errors import WorkflowError

EXPERIMENT = WORKSPACE / "3_标题和封面生成/parallel_experiments/raw_titles_80_20_v001"


def test_all_examples_and_approved_prompt_are_preserved_verbatim() -> None:
    profile = ProductionProfile(WORKSPACE)
    source = EXPERIMENT / "batches/b003_workflow_latest/all_examples_raw.json"
    assert (profile.raw_titles / "examples.json").read_bytes() == source.read_bytes()
    template = EXPERIMENT / "prompt_versions/v002_all_examples/task.txt"
    assert (profile.raw_titles / "title.txt").read_bytes() == template.read_bytes()
    prompt = profile.title_prompt({"source_titles": [], "transcript": []})
    example_block = prompt.split("【历史投稿标题原文】\n", 1)[1].split("【当前视频", 1)[0]
    examples = json.loads(example_block)
    assert len(examples) == 1195 and examples == json.loads(source.read_text(encoding="utf-8"))
    assert "{{raw_titles_json}}" not in prompt and "{{video_json}}" not in prompt


@pytest.mark.parametrize("filename", ["title.txt", "examples.json", "cover_plan.txt"])
def test_profile_changes_fail_before_model_calls(tmp_path: Path, filename: str) -> None:
    profile = ProductionProfile(tmp_path)
    shutil.copytree(ProductionProfile(WORKSPACE).raw_titles, profile.raw_titles)
    path = profile.raw_titles / filename
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(WorkflowError, match="哈希不匹配"):
        if filename == "cover_plan.txt":
            profile.cover_prompt({})
        else:
            profile.title_prompt({})


@pytest.mark.parametrize("text", ["😅日本的新安排，还得看条件", "不带数字也不用冒号", "中" * 80])
def test_titles_allow_free_structure_and_preserve_text(text: str) -> None:
    result = parse_titles(encode(raw_titles()))
    result.titles[0].text = text
    assert parse_titles(result.model_dump_json()).titles[0].text == text


@pytest.mark.parametrize("text", ["中" * 81, "", " \n "])
def test_invalid_titles_fail_without_truncating(text: str) -> None:
    result = parse_titles(encode(raw_titles()))
    result.titles[0].text = text
    with pytest.raises((ValidationError, WorkflowError)):
        parse_titles(result.model_dump_json())
    assert result.titles[0].text == text


def test_title_count_identity_and_unrequested_fields_are_rejected() -> None:
    titles = parse_titles(encode(raw_titles()))
    titles.titles[1].title_id = "TITLE1"
    with pytest.raises(WorkflowError):
        parse_titles(titles.model_dump_json())
    titles.titles.pop()
    with pytest.raises(ValidationError):
        parse_titles(titles.model_dump_json())
    value = raw_titles()
    value["reference_answer"] = "不得带入当前答案"
    with pytest.raises(ValidationError):
        parse_titles(encode(value))
    value = raw_titles()
    value["recommended_title_id"] = "UNKNOWN"
    with pytest.raises(ValidationError):
        parse_titles(encode(value))


def test_cover_must_preserve_titles_recommendation_and_real_quotes() -> None:
    titles, plan = parse_titles(encode(raw_titles())), CoverPlanning.model_validate(cover_plan())
    check_title_binding(plan, titles, transcript())
    plan.titles[0].text += "！"
    with pytest.raises(WorkflowError, match="逐字保留"):
        check_title_binding(plan, titles, transcript())
    plan.titles[0].text = titles.titles[0].text
    plan.recommended_title_id = "TITLE2"
    with pytest.raises(WorkflowError, match="推荐ID"):
        check_title_binding(plan, titles, transcript())
    plan.recommended_title_id = titles.recommended_title_id
    plan.titles[0].evidence[0].quote = "引文不存在"
    with pytest.raises(WorkflowError, match="引文"):
        check_title_binding(plan, titles, transcript())
