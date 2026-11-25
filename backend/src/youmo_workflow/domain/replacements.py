from pydantic import Field

from youmo_workflow.domain.models import Contract


class Replacement(Contract):
    old_publication_id: str
    old_manifest_hash: str
    old_remote_id: str
    old_content_id: str
    new_content_id: str
    batch_id: str
    packaging_task_id: str
    superseded_task_ids: list[str] = Field(min_length=1)
    previous_task_states: dict[str, str]
    authorized_at: str
    version: str
