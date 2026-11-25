import hashlib

from sqlalchemy.orm import Session

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.hard_replacement import HARD_REPLACEMENT_PREFIX, HardReplacement
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.review_skip import SupplementalReviewSkip
from youmo_workflow.storage.models import Batch, Publication, Task, WorkflowSetting


def authorized_review_skip(db: Session, task: Task, content_id: str) -> bool:
    batch = db.get(Batch, task.batch_id) if task.batch_id else None
    if batch and batch.status == "cancelled":
        return False
    if (
        batch
        and batch.execution_json
        and BatchExecution.model_validate_json(batch.execution_json).skip_review_rejections
    ):
        return True
    if batch and supplemental_authorized(db, task, batch, content_id):
        return True
    setting = db.get(WorkflowSetting, HARD_REPLACEMENT_PREFIX + content_id)
    if setting is None or task.kind != "finalize_private":
        return False
    record = HardReplacement.model_validate_json(setting.value_json)
    publication = db.get(
        Publication, FinalizePrivate.model_validate_json(task.input_json).publication_id
    )
    if publication is None:
        return False
    manifest = PublicationManifest.model_validate_json(publication.manifest_json)
    return bool(
        record.skip_review_rejections
        and record.owner_manages_removal
        and record.batch_id == task.batch_id
        and record.new_content_id == content_id == publication.content_id
        and record.media_sha256 == manifest.media_ref.sha256
        and manifest.subtitle_ref
        and record.subtitle_sha256 == manifest.subtitle_ref.sha256
        and manifest.source_url == f"https://www.youtube.com/watch?v={record.video_id}"
    )


def supplemental_authorized(db: Session, task: Task, batch: Batch, content_id: str) -> bool:
    setting = db.get(WorkflowSetting, "review_skip_authorization:" + task.id)
    if setting is None or batch.execution_json is None:
        return False
    record = SupplementalReviewSkip.model_validate_json(setting.value_json)
    return (
        record.task_id == task.id
        and record.batch_id == batch.id
        and record.content_id == content_id
        and record.error_code == task.error_code
        and record.attempts == task.attempts
        and not record.publication_succeeded
        and record.execution_sha256 == digest(batch.execution_json)
        and record.input_sha256 == digest(task.input_json)
        and record.output_sha256 == digest(task.output_json)
    )


def digest(value: str | None) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()
