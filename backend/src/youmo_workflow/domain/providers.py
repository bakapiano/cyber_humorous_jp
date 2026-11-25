from typing import Literal, Protocol

from pydantic import Field

from youmo_workflow.domain.content import Citation
from youmo_workflow.domain.models import Contract, DiscoveryPage, DiscoveryVideo, VideoDTO


class SelectionRequest(Contract):
    batch_id: str
    items: list[VideoDTO]
    candidate_limit: int
    max_approved: int


class ArtifactRef(Contract):
    artifact_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SelectedContent(Contract):
    content_id: str
    video_id: str
    original_title: str
    original_media_ref: ArtifactRef
    transcript_ref: ArtifactRef
    translation_ref: ArtifactRef
    evidence: list[str]
    expires_at: str


class SelectionOutput(Contract):
    batch_id: str
    selected: list[SelectedContent]
    rejected_ids: list[str]


class PackagingRequest(Contract):
    content: SelectedContent


class PackagingOutput(Contract):
    content_id: str
    title: str = Field(min_length=1, max_length=80)
    cover_ref: ArtifactRef
    original_media_ref: ArtifactRef
    title_evidence: list[Citation] = Field(default_factory=list[Citation])
    editorial_angle: str = ""
    cover_timestamp: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    packaging_version: str = "legacy"
    warnings: list[str] = Field(default_factory=list)


class SelectionProvider(Protocol):
    def select(self, request: SelectionRequest) -> SelectionOutput: ...


class PackagingProvider(Protocol):
    def package(self, request: PackagingRequest) -> PackagingOutput: ...


class ChannelProvider(Protocol):
    def collect(self, handle: str, since: str) -> list[DiscoveryVideo]: ...

    def lookup(self, video_ids: list[str]) -> list[DiscoveryVideo]: ...


class HomeProvider(Protocol):
    def discover(self) -> DiscoveryPage: ...


class ReadyContent(Contract):
    content: SelectedContent
    packaging: PackagingOutput
    editing_revision: Literal["v004", "v005"] = Field(
        default="v004", exclude_if=lambda value: value == "v004"
    )
