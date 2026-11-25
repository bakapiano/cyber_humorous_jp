from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ClockTime = Annotated[str, Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Policy(Contract):
    initial_selection_version: Literal["legacy", "v004", "v008"] = "legacy"
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    daily_trigger: ClockTime = "10:00"
    channel_poll_minutes: int = Field(default=30, ge=5)
    candidate_window_hours: int = Field(default=72, ge=1, le=720)
    candidate_limit: int = Field(default=6, ge=1, le=50)
    max_approved: int = Field(default=3, ge=1, le=50)
    publish_slots: list[ClockTime] = Field(default_factory=lambda: ["13:00", "16:00", "19:00"])
    max_new_publications_per_day: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        if self.initial_selection_version in {"v004", "v008"} and (
            self.candidate_limit,
            self.max_approved,
        ) != (30, 30):
            raise ValueError("当前初筛固定 Top30，复筛直通不再缩减数量")
        if self.max_approved > self.candidate_limit:
            raise ValueError("二筛上限不能大于首筛候选上限")
        if len(set(self.publish_slots)) != len(self.publish_slots):
            raise ValueError("发布时段不能重复")
        if self.max_new_publications_per_day > len(self.publish_slots):
            raise ValueError("每日发布上限不能大于可用时段数")
        return self


class SourceDTO(Contract):
    id: str
    name: str
    url: str
    evidence_count: int
    enabled: bool
    last_checked: str | None = None
    last_error: str | None = None
    video_count: int = 0


class DiscoveryVideo(Contract):
    video_id: str = Field(pattern=r"^[A-Za-z0-9_-]{11}$")
    title: str = Field(min_length=1)
    channel_id: str | None = None
    channel_title: str | None = None
    published_at: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    thumbnail_url: str | None = None
    origin: Literal["channel", "home", "manual"] = "channel"


class DiscoveryPage(Contract):
    items: list[DiscoveryVideo]
    continuation: str | None = None
    skipped_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class VideoDTO(DiscoveryVideo):
    source_id: str
    discovered_at: str
    revision: int
    screened: bool


class VideoPage(Contract):
    items: list[VideoDTO]
    total: int


class TaskDTO(Contract):
    id: str
    kind: str
    status: str
    batch_id: str | None
    attempts: int
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str


class BatchDTO(Contract):
    id: str
    local_date: str
    status: str
    snapshot_at: str
    candidate_count: int
    policy: Policy
    error_code: str | None
    error_message: str | None
    trigger_mode: Literal["legacy", "manual", "automatic"] = "legacy"
    publications_per_batch: int | None = None


class BatchDetail(BatchDTO):
    items: list[VideoDTO]


class Dashboard(Contract):
    sources: int
    enabled_sources: int
    videos: int
    eligible_videos: int
    batches: int
    failed_tasks: int
    policy: Policy
    automation_enabled: bool
    publication_enabled: bool
    selection_provider: Literal["not_implemented", "v004_title_recall", "v008_anchor_rescue"] = (
        "v008_anchor_rescue"
    )
    rescreen_provider: Literal["passthrough"] = "passthrough"
    content_preparation_provider: Literal["not_implemented", "youtube_local_asr"] = (
        "youtube_local_asr"
    )
    packaging_provider: Literal["not_implemented", "raw_history_poster_v001"] = (
        "raw_history_poster_v001"
    )
    editing_provider: Literal["v003_ranges"] = "v003_ranges"


class SessionInfo(Contract):
    csrf_token: str


class ErrorResponse(Contract):
    code: str
    message: str


class Health(Contract):
    status: Literal["ok"] = "ok"
    service: str = "youmo-workflow"


class SourceUpdate(Contract):
    enabled: bool
