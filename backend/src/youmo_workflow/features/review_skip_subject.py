from sqlalchemy.orm import Session

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.providers import ReadyContent
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.features.video_review import verify_video_rejection
from youmo_workflow.storage.models import Publication, Task


def review_subject(db: Session, task: Task) -> tuple[str, str] | None:
    if task.kind == "editing" and task.error_code == "SUBTITLE_LLM_REVIEW_BLOCKED":
        content = ReadyContent.model_validate_json(task.input_json).content
        return content.video_id, content.content_id
    if task.kind != "finalize_private" or task.error_code != "VIDEO_REVIEW_REJECTED":
        return None
    request = FinalizePrivate.model_validate_json(task.input_json)
    publication = db.get(Publication, request.publication_id)
    if (
        publication is None
        or publication.manifest_hash != request.manifest_hash
        or publication.confirmed_hash != request.manifest_hash
        or not publication.remote_id
    ):
        raise WorkflowError("REVIEW_SKIP_UNVERIFIED", "审核跳过任务没有匹配的已确认稿件。")
    manifest = PublicationManifest.model_validate_json(publication.manifest_json)
    if manifest.subtitle_mode != "hard" or not manifest.source_url.startswith(
        "https://www.youtube.com/watch?v="
    ):
        return None
    verify_video_rejection(db, publication)
    return manifest.source_url.removeprefix("https://www.youtube.com/watch?v="), manifest.content_id
