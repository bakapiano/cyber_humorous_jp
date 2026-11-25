from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from youmo_workflow.adapters.packaging_models import CoverPlanning
from youmo_workflow.adapters.packaging_validation import check_planning, require
from youmo_workflow.domain.content import Transcript
from youmo_workflow.domain.providers import PackagingRequest

TitleId = Literal["TITLE1", "TITLE2", "TITLE3"]


class RawTitle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_id: TitleId
    text: str = Field(min_length=1, max_length=80)


class RawTitles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titles: list[RawTitle] = Field(min_length=3, max_length=3)
    recommended_title_id: TitleId


def parse_titles(raw: str) -> RawTitles:
    result = RawTitles.model_validate_json(raw)
    require(
        [row.title_id for row in result.titles] == ["TITLE1", "TITLE2", "TITLE3"],
        "标题必须依次包含唯一的TITLE1、TITLE2、TITLE3。",
    )
    require(all(row.text.strip() for row in result.titles), "标题不能为空白。")
    return result


def title_input(request: PackagingRequest, transcript: Transcript) -> dict[str, JsonValue]:
    return {
        "source_titles": [{"title": request.content.original_title}],
        "transcript": [
            {"start": row.start, "end": row.end, "text": row.text} for row in transcript.segments
        ],
    }


def check_title_binding(plan: CoverPlanning, titles: RawTitles, transcript: Transcript) -> None:
    require(
        [(row.title_id, row.text) for row in plan.titles]
        == [(row.title_id, row.text) for row in titles.titles]
        and plan.recommended_title_id == titles.recommended_title_id,
        "封面阶段必须逐字保留已生成的三个标题、顺序及推荐ID。",
    )
    check_planning(plan, transcript, poster=True)
