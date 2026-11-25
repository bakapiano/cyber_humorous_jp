from typing import Literal, Protocol

from pydantic import Field

from youmo_workflow.domain.models import Contract, VideoDTO
from youmo_workflow.domain.providers import SelectedContent, SelectionOutput

SelectionVersion = Literal["v004", "v008"]


class InitialRequest(Contract):
    batch_id: str
    snapshot_at: str
    items: list[VideoDTO]


class ModelCandidate(Contract):
    candidate_id: str
    title: str
    channel: str
    duration_iso: str


class ModelExample(Contract):
    example_id: str
    title: str


class RankingRow(Contract):
    candidate_id: str
    angle: str = Field(min_length=1, max_length=35)
    evidence_field: Literal["title"]
    evidence: str = Field(min_length=5, max_length=55)
    verify: str = Field(min_length=1, max_length=35)


class ModelRanking(Contract):
    ranked_candidates: list[RankingRow]


class RankedCandidate(RankingRow):
    rank: int
    video: VideoDTO
    evidence_matches_title: bool


class InitialOutput(Contract):
    batch_id: str
    version: SelectionVersion = "v004"
    input_sha256: str
    prompt_sha256: str
    rank_count: int
    shortlist_limit: int
    ranked: list[RankedCandidate]
    shortlisted_ids: list[str]
    not_shortlisted_ids: list[str]
    content_verified: Literal[False] = False
    composition: Literal["baseline", "anchor_rescue", "baseline_fallback"] = "baseline"
    fallback_reason: Literal["provider_content_filter"] | None = None
    component_hashes: dict[str, str] = Field(default_factory=dict)


class InitialProvider(Protocol):
    def rank(self, request: InitialRequest) -> InitialOutput: ...


class RescreenProvider(Protocol):
    def review(self, request: InitialOutput) -> InitialOutput: ...


class ContentPreparationProvider(Protocol):
    def prepare(self, request: InitialOutput) -> SelectionOutput: ...


class SingleContentProvider(Protocol):
    def prepare_video(self, video: VideoDTO) -> SelectedContent: ...


class SelectionStagesDTO(Contract):
    active_version: SelectionVersion = "v008"
    initial: InitialOutput | None = None
    rescreen: InitialOutput | None = None
    rescreen_mode: Literal["passthrough"] = "passthrough"
    content_preparation: Literal["not_implemented", "youtube_local_asr"] = "youtube_local_asr"
    history: list[InitialOutput] = Field(default_factory=list[InitialOutput])
