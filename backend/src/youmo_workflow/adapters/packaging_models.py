from typing import Literal

from pydantic import BaseModel, Field

from youmo_workflow.domain.content import Citation


class Title(BaseModel):
    title_id: str
    text: str = Field(min_length=1, max_length=80)
    evidence: list[Citation] = Field(min_length=1)


class CoverRegion(BaseModel):
    region_id: str
    start_sec: float = Field(ge=0, allow_inf_nan=False)
    end_sec: float = Field(gt=0, allow_inf_nan=False)
    evidence_segment_ids: list[str] = Field(min_length=1)


class CoverPhrase(BaseModel):
    phrase_id: str
    text: str = Field(min_length=2, max_length=24)
    kind: Literal["fact", "comment", "question"]
    evidence_segment_ids: list[str] = Field(min_length=1)


class CoverPlanning(BaseModel):
    titles: list[Title] = Field(min_length=3, max_length=3)
    recommended_title_id: str
    core_angle: str
    cover_regions: list[CoverRegion] = Field(min_length=1, max_length=3)
    cover_phrases: list[CoverPhrase] = Field(min_length=2, max_length=4)
    warnings: list[str] = Field(default_factory=list)


class CoverVariant(BaseModel):
    variant_id: Literal["A", "B"]
    title_id: str
    phrase_id: str
    lines: list[str] = Field(min_length=1, max_length=3)
    anchor: Literal["left_middle", "right_middle", "center_middle"]
    red_keywords: list[str] = Field(default_factory=list, max_length=3)


class CoverChoice(BaseModel):
    selected_frame_id: str
    visible_content: str = Field(min_length=4)
    selection_reason: str
    subject_box: tuple[float, float, float, float]
    recommended_title_id: str
    covers: list[CoverVariant] = Field(min_length=2, max_length=2)
    checks: dict[str, bool]
    warnings: list[str]


class Frame(BaseModel):
    frame_id: str = Field(pattern=r"^F[0-9]{3}$")
    region_id: str
    timestamp_sec: float
    sha256: str


class FrameIndex(BaseModel):
    frames: list[Frame]


class RenderSettings(BaseModel):
    layout: Literal["legacy", "poster"] = "legacy"
    highlight_fill: str = "#FF3B30"
    background_brightness: float = Field(default=1, ge=0.4, le=1)
    canvas: tuple[int, int]
    font_path: str
    max_font_size: int
    min_font_size: int
    max_text_width: int
    max_text_height: int
    top: int
    side_margin: int
    line_spacing: int
    fill: str
    stroke: str
    stroke_width: int
