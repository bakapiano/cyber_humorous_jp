"""Explicitly authorized continuation after a read-only receipt reconciliation."""

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.tasks import add_task
from youmo_workflow.storage.models import Publication, Task


def resume_recovered_upload(
    factory: sessionmaker[Session], request: FinalizePrivate, remote_id: str, at: datetime
) -> str:
    with factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        publication = db.get(Publication, request.publication_id)
        if (
            publication is None
            or publication.remote_id != remote_id
            or publication.manifest_hash != request.manifest_hash
            or publication.confirmed_hash != request.manifest_hash
            or publication.status
            not in {"uploaded", "reviewing", "awaiting_subtitles", "completed"}
        ):
            raise WorkflowError("RECOVERY_NOT_VERIFIED", "仅可续接已核对远端回执和发布包的稿件。")
        upload = db.scalar(select(Task).where(Task.unique_key == f"batch-publish:{publication.id}"))
        if upload is None or upload.status not in {"failed", "unknown", "succeeded"}:
            raise WorkflowError("RECOVERY_TASK_NOT_READY", "投稿任务缺失或仍在执行，未排队字幕。")
        if FinalizePrivate.model_validate_json(upload.input_json) != request:
            raise WorkflowError("CONFIRMATION_STALE", "旧投稿任务与恢复发布包不匹配。")
        finish_recovered_task(upload, remote_id, at)
        finalizer = add_task(
            db, "finalize_private", f"finalize:{publication.id}:{request.manifest_hash}", stamp(at)
        )
        if finalizer.input_json == "{}":
            finalizer.input_json = request.model_dump_json()
            finalizer.batch_id, finalizer.max_attempts = upload.batch_id, 60
        elif FinalizePrivate.model_validate_json(finalizer.input_json) != request:
            raise WorkflowError("CONFIRMATION_STALE", "已有字幕任务与恢复发布包不匹配。")
        return finalizer.id


def finish_recovered_task(task: Task, remote_id: str, at: datetime) -> None:
    if task.status == "succeeded":
        return
    task.output_json = json.dumps(
        {
            "remote_id": remote_id,
            "recovery": {
                "at": stamp(at),
                "previous_status": task.status,
                "previous_error_code": task.error_code,
                "previous_error_message": task.error_message,
                "previous_output_json": task.output_json,
                "upload_repeated": False,
            },
        },
        ensure_ascii=False,
    )
    task.status, task.updated_at = "succeeded", stamp(at)
    task.error_code = task.error_message = None
    task.lease_token = task.lease_until = None
