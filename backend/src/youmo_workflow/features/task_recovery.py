"""Explicit per-stage retries preserve history, capacity, and unknown-write isolation."""

import json
from datetime import datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.orm import Session

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.pipeline_gates import blocked_kinds
from youmo_workflow.features.production_buffer import (
    PRODUCER_KINDS,
    buffer_used,
    require_buffer_capacity,
)
from youmo_workflow.storage.models import Batch, Publication, Task, WorkflowSetting


def prepare_retry(db: Session, task: Task, at: datetime) -> None:
    batch = db.get(Batch, task.batch_id) if task.batch_id else None
    if batch and (batch.reservations_released or batch.status == "cancelled"):
        raise WorkflowError("BATCH_RETIRED", "已取消或退役批次只读保留，不能恢复任务。")
    if task.kind in blocked_kinds(db):
        raise WorkflowError("PIPELINE_STAGE_HELD", "先解决并解除此阶段的依赖暂停，再重试单条任务。")
    if task.attempts >= task.max_attempts:
        raise WorkflowError(
            "RETRY_BUDGET_EXHAUSTED", "本阶段任务次数已用尽，不能重置历史追加执行。"
        )
    reactivate_task(db, task)
    db.add(
        WorkflowSetting(
            id=f"task_retry:{task.id}:{uuid4().hex}",
            revision=1,
            value_json=json.dumps(
                {
                    "task_id": task.id,
                    "attempts": task.attempts,
                    "max_attempts": task.max_attempts,
                    "error_code": task.error_code,
                    "error_message": task.error_message,
                    "at": stamp(at),
                },
                ensure_ascii=False,
            ),
        )
    )


def reactivate_task(db: Session, task: Task) -> None:
    if task.kind not in PRODUCER_KINDS:
        task.status = "pending"
        return
    before = buffer_used(db)
    task.status = "pending"
    db.flush()
    if buffer_used(db) > before:
        require_buffer_capacity(db, 0)


def quarantine_upload(db: Session, task: Task) -> bool:
    try:
        request = FinalizePrivate.model_validate_json(task.input_json)
    except ValidationError:
        return False
    publication = db.get(Publication, request.publication_id)
    if (
        publication
        and publication.status == "submitting"
        and not publication.remote_id
        and publication.manifest_hash == request.manifest_hash
    ):
        publication.status = "unknown"
        return True
    return False
