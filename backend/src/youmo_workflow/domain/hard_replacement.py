from pydantic import Field

from youmo_workflow.domain.editing import EditingOutput
from youmo_workflow.domain.models import Contract


class HardReplacementRequest(Contract):
    source_task_id: str
    new_content_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    old_publication_ids: list[str] = Field(min_length=1, max_length=10)
    editing: EditingOutput
    owner_manages_removal: bool


class HardReplacement(Contract):
    request_sha256: str
    source_task_id: str
    prepare_task_id: str
    batch_id: str
    new_content_id: str
    reused_content_id: str
    video_id: str
    old_manifests: dict[str, str]
    old_remote_ids: list[str]
    media_sha256: str
    subtitle_sha256: str
    authority: str
    authorized_at: str
    owner_manages_removal: bool = True
    deletion_verified: bool = False
    skip_review_rejections: bool = True


HARD_REPLACEMENT_PREFIX = "hard_replacement:"
