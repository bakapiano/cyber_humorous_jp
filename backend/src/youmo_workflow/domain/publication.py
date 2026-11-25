from typing import Literal

from pydantic import Field

from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.providers import ArtifactRef


class PublicationManifest(Contract):
    content_id: str
    title: str = Field(min_length=1, max_length=80)
    media_ref: ArtifactRef
    cover_ref: ArtifactRef
    subtitle_ref: ArtifactRef | None = None
    subtitle_mode: Literal["soft", "hard"]
    source_url: str
    expires_at: str
    visibility: Literal["private", "public"] = "private"
    rights_confirmed: bool = False
    rights_note: str = ""
    test_only: bool = False
    burn_in_ref: ArtifactRef | None = Field(default=None, exclude_if=lambda v: v is None)


class PublicationDTO(Contract):
    id: str
    content_id: str
    title: str
    manifest_hash: str
    confirmed: bool
    status: str
    visibility: str
    remote_id: str | None
    test_only: bool
    created_at: str


class Confirmation(Contract):
    manifest_hash: str


class ArtifactDTO(Contract):
    artifact_id: str
    kind: str
    sha256: str
    size_bytes: int
    test_only: bool
    available: bool = True


class SubmitPlan(Contract):
    publication_id: str
    execution: Literal["dry_run"] = "dry_run"
    title: str
    visibility: str
    manifest_hash: str
    media_verified: bool
    cover_verified: bool
    subtitle_verified: bool
    live_enabled: bool


class ProbeResult(Contract):
    duration_seconds: float
    has_video: bool
    has_audio: bool
