from typing import Literal, Protocol, Self

from pydantic import BaseModel, Field, model_validator

from youmo_workflow.domain.content import Citation
from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.providers import ArtifactRef, ReadyContent


class Clip(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    reason: str = Field(min_length=1)
    evidence: list[Citation] = Field(min_length=1)


class MainClip(Clip):
    scope: Literal["full_report", "topic_section"]


class OmittedClip(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    reason: str


class TitleAlignment(BaseModel):
    title: str
    supported: bool
    evidence_segment_ids: list[str]
    recap_support: str
    context_preserved: bool


class EditPlan(BaseModel):
    topic: str = Field(min_length=1)
    source_timebase: Literal["local_video_seconds"]
    quick_recap: list[Clip] = Field(min_length=1, max_length=2)
    main: MainClip
    omitted_sections: list[OmittedClip]
    checks: dict[str, bool]
    warnings: list[str]
    title_alignment: TitleAlignment


class CutRange(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)


class RangePlan(BaseModel):
    quick_recap: list[CutRange] = Field(min_length=1, max_length=1)
    main: CutRange


class TimelinePart(Contract):
    part: Literal["quick_recap", "main"]
    source_start: float
    source_end: float
    output_start: float
    output_end: float


class EditingOutput(Contract):
    content_id: str
    original_media_ref: ArtifactRef
    media_ref: ArtifactRef
    subtitle_ref: ArtifactRef
    plan_ref: ArtifactRef
    timeline: list[TimelinePart] = Field(min_length=1)
    title: str
    version: str = "v003_ranges"
    subtitle_mode: Literal["soft", "hard"] = Field(default="soft", exclude_if=lambda v: v == "soft")
    burn_in_ref: ArtifactRef | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def timeline_matches_version(self) -> Self:
        if self.version != "v005_hardsub" and len(self.timeline) < 2:
            raise ValueError("Legacy editing output requires recap and main parts")
        if self.version == "v005_hardsub" and [part.part for part in self.timeline] not in (
            ["main"],
            ["quick_recap", "main"],
        ):
            raise ValueError("v005 requires main-only or recap followed by main")
        return self


class EditedContent(ReadyContent):
    editing: EditingOutput


class EditingProvider(Protocol):
    def edit(self, request: ReadyContent) -> EditingOutput: ...
