"""Transactional producer admission, independent of delivery progress."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.features.batch_task_view import effective_batch_tasks
from youmo_workflow.features.buffer_slots import released_video_slots, terminal_unbound_package
from youmo_workflow.features.pipeline_gates import blocked_kinds
from youmo_workflow.features.pipeline_storage import disk_ready
from youmo_workflow.features.task_skips import verified_skips
from youmo_workflow.storage.models import Artifact, Batch, Publication, Task

BUFFER_LIMIT = 50
PRODUCER_KINDS = (
    "selection",
    "initial_selection",
    "rescreen",
    "prepare_content",
    "prepare_video",
    "dispatch_production",
    "packaging",
    "editing",
    "prepare_publication",
)
CONSUMER_KINDS = ("cleanup_publication", "finalize_private", "publish", "cleanup_batch")


def active_producer(db: Session) -> str | None:
    return db.scalar(
        select(Task.batch_id)
        .where(
            Task.batch_id.is_not(None),
            Task.kind.in_(PRODUCER_KINDS),
            Task.status.in_(["pending", "running", "retry_wait"]),
        )
        .order_by(Task.created_at)
        .limit(1)
    )


def producer_failed(db: Session, batch: Batch) -> bool:
    tasks = effective_batch_tasks(db, batch.id)
    if not tasks:
        return batch.status in {"failed", "unknown"}
    return any(
        task.kind in PRODUCER_KINDS and task.status in {"failed", "unknown"} for task in tasks
    )


def reserved_slots(db: Session, batch: Batch, execution: BatchExecution) -> int:
    tasks = effective_batch_tasks(db, batch.id)
    if selection_failed_without_outputs(tasks):
        return 0
    if any(task.kind == "cleanup_batch" and task.status == "succeeded" for task in tasks):
        return 0
    released = released_video_slots(db, tasks, verified_skips(db, batch.id, tasks))
    return max(0, execution.publications_per_batch - released)


def buffer_used(db: Session) -> int:
    reserved_batches: set[str] = set()
    count = 0
    for batch in db.scalars(select(Batch).where(Batch.execution_json.is_not(None))):
        execution = BatchExecution.model_validate_json(batch.execution_json or "{}")
        if execution.producer_consumer and batch.status != "cancelled":
            reserved_batches.add(batch.id)
            count += reserved_slots(db, batch, execution)
    bound = {
        task.unique_key.removeprefix("batch-publish:")
        for task in db.scalars(
            select(Task).where(
                Task.batch_id.in_(reserved_batches),
                Task.kind == "publish",
            )
        )
    }
    for publication in db.scalars(select(Publication).where(Publication.id.not_in(bound))):
        manifest = PublicationManifest.model_validate_json(publication.manifest_json)
        if manifest.test_only or terminal_unbound_package(db, publication):
            continue
        media = db.get(Artifact, manifest.media_ref.artifact_id)
        if media and media.cleaned_at:
            continue
        # Legacy undelivered items count too; delivered hard videos retain a
        # slot until their actual local-media cleanup, not merely receipt arrival.
        if not publication.remote_id or manifest.subtitle_mode == "hard":
            count += 1
    return count


def require_buffer_capacity(db: Session, slots: int) -> None:
    used = buffer_used(db)
    if used + slots > BUFFER_LIMIT:
        raise WorkflowError(
            "PRODUCTION_BUFFER_FULL", f"本地制作/待投名额已用{used}/{BUFFER_LIMIT}，等待投递清理。"
        )


def buffer_available(db: Session, slots: int, *, manual: bool) -> bool:
    try:
        if "initial_selection" in blocked_kinds(db):
            raise WorkflowError("PIPELINE_STAGE_HELD", "选题依赖暂停，请先处理模型或存储故障。")
        if not disk_ready():
            raise WorkflowError("DISK_SPACE_LOW", "剩余磁盘不足2GB，暂停新增制作，投稿清理继续。")
        require_buffer_capacity(db, slots)
    except WorkflowError:
        if manual:
            raise
        return False
    return True


def selection_failed_without_outputs(tasks: list[Task]) -> bool:
    return bool(
        tasks
        and all(
            task.kind in {"selection", "initial_selection", "rescreen", "dispatch_production"}
            for task in tasks
        )
        and all(task.status in {"succeeded", "failed", "unknown"} for task in tasks)
        and any(task.status in {"failed", "unknown"} for task in tasks)
    )
