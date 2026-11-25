from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.cleanup import CleanupPlan
from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.features.batch_task_view import effective_batch_tasks
from youmo_workflow.features.task_skips import verified_skips
from youmo_workflow.storage.models import Batch, Task


def refresh_batch_progress(
    factory: sessionmaker[Session], batch_id: str | None, at: datetime | None = None
) -> None:
    if batch_id is None:
        return
    with factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        batch = db.get(Batch, batch_id)
        if batch is None or batch.execution_json is None or batch.status == "cancelled":
            return
        tasks = effective_batch_tasks(db, batch_id)
        skipped = verified_skips(db, batch_id, tasks)
        errors = [task for task in tasks if task.status in {"failed", "unknown"}]
        if errors:
            batch.status = (
                "running"
                if any(task.status in {"pending", "running", "retry_wait"} for task in tasks)
                else "failed"
            )
            batch.error_code = errors[0].error_code
            batch.error_message = errors[0].error_message
        elif tasks and all(task.status in {"succeeded", "skipped"} for task in tasks):
            expected = BatchExecution.model_validate_json(
                batch.execution_json
            ).publications_per_batch
            if (
                sum(
                    task.kind == "finalize_private" and task.status == "succeeded" for task in tasks
                )
                + len(skipped)
                == expected
            ):
                if any(task.kind == "cleanup_batch" for task in tasks):
                    batch.status = "completed_with_skips" if skipped else "completed"
                    batch.error_code = batch.error_message = None
                else:
                    enqueue_cleanup(db, batch, stamp(at or now_utc()))
            else:
                batch.status = "failed"
                batch.error_code, batch.error_message = (
                    "BATCH_PUBLICATIONS_INCOMPLETE",
                    "已结束任务没有覆盖本批投稿数；未自动补投。",
                )
        else:
            batch.status = "running"
            batch.error_code = batch.error_message = None


def enqueue_cleanup(db: Session, batch: Batch, at: str) -> None:
    db.add(
        Task(
            id=uuid4().hex,
            unique_key=f"cleanup:{batch.id}",
            kind="cleanup_batch",
            batch_id=batch.id,
            input_json=CleanupPlan(batch_id=batch.id).model_dump_json(),
            created_at=at,
            updated_at=at,
            next_run_at=at,
        )
    )
    batch.status = "cleaning"
