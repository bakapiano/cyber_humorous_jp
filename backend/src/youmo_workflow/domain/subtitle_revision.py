from pydantic import Field

from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.providers import ArtifactRef


class SubtitleRevisionDraft(Contract):
    publication_id: str
    revised_ref: ArtifactRef
    rules_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authority: str = Field(min_length=10)


class SubtitleRevision(SubtitleRevisionDraft):
    revision_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    manifest_hash: str
    remote_id: str
    base_ref: ArtifactRef
    previous_ref: ArtifactRef
    rejected_subtitle_id: str = Field(pattern=r"^[1-9][0-9]{0,24}$")
    rejection_reason: str
    authorized_at: str


class ActiveSubtitleRevision(Contract):
    revision_id: str = Field(pattern=r"^[0-9a-f]{32}$")
