"""Receipt-bound single-video cleanup, retaining all non-media history."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from youmo_workflow.domain.cleanup import CleanupPlan
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.features.tasks import add_task
from youmo_workflow.storage.models import Publication, Task


def enqueue_delivery_cleanup(db: Session, publication: Publication, task: Task, at: str) -> None:
    if not task.batch_id:
        return
    child = add_task(db, "cleanup_publication", f"delivery-cleanup:{publication.id}", at)
    if child.input_json == "{}":
        child.batch_id = task.batch_id
        child.input_json = CleanupPlan(
            batch_id=task.batch_id, publication_id=publication.id
        ).model_dump_json()


def delivery_contents(db: Session, task: Task, plan: CleanupPlan) -> list[str]:
    publication = db.get(Publication, plan.publication_id)
    if not publication or task.batch_id != plan.batch_id:
        raise WorkflowError("CLEANUP_NOT_ELIGIBLE", "缺少匹配的单条投递记录。")
    manifest = PublicationManifest.model_validate_json(publication.manifest_json)
    finalizer = db.scalar(
        select(Task).where(
            Task.unique_key == f"finalize:{publication.id}:{publication.manifest_hash}",
            Task.batch_id == plan.batch_id,
            Task.status == "succeeded",
        )
    )
    if (
        not finalizer
        or not publication.remote_id
        or manifest.subtitle_mode != "hard"
        or publication.status not in {"uploaded", "completed"}
        or publication.confirmed_hash != publication.manifest_hash
        or FinalizePrivate.model_validate_json(finalizer.input_json).manifest_hash
        != publication.manifest_hash
    ):
        raise WorkflowError("CLEANUP_NOT_ELIGIBLE", "单条硬字幕稿尚未按唯一回执完成，保留视频。")
    return [publication.content_id]
