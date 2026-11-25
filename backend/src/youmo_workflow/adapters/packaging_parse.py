from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from youmo_workflow.adapters.packaging_models import CoverPlanning
from youmo_workflow.adapters.packaging_validation import require
from youmo_workflow.adapters.production_files import encode, save


def parse_planning(raw: str, directory: Path) -> CoverPlanning:
    value = TypeAdapter(dict[str, JsonValue]).validate_json(raw)
    titles = value.get("titles")
    require(isinstance(titles, list) and len(titles) == 3, "封面规划需包含三个候选。")
    assert isinstance(titles, list)
    all_absent = all(
        isinstance(row, dict) and "title_id" not in row and "id" not in row for row in titles
    )
    mappings: list[JsonValue] = []
    for index, row in enumerate(titles, 1):
        require(isinstance(row, dict), "标题对象格式无效。")
        assert isinstance(row, dict)
        if "text" not in row and isinstance(row.get("title"), str):
            row["text"] = row["title"]
            mappings.append(f"TITLE{index}: title -> text")
        if "id" in row:
            require(row.get("title_id", row["id"]) == row["id"], "标题ID别名冲突。")
            row["title_id"] = row["id"]
            mappings.append(f"TITLE{index}: id -> title_id")
        elif all_absent:
            require(
                value.get("recommended_title_id") in {"TITLE1", "TITLE2", "TITLE3"},
                "缺失ID时必须有明确顺序推荐。",
            )
            row["title_id"] = f"TITLE{index}"
            mappings.append(f"TITLE{index}: declared three-item ordering")
    result = CoverPlanning.model_validate(value)
    save(
        directory / "schema_adapter.json",
        encode({"mappings": mappings, "title_text_edited": False}),
    )
    save(directory / "cover_plan.json", result.model_dump_json())
    return result
