"""Explicit, audited queue cutover; keep media, manifests and model journals intact."""

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from youmo_workflow.domain.batch_plan import BatchPlan
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.time import parse_stamp, stamp
from youmo_workflow.features.production_buffer import PRODUCER_KINDS
from youmo_workflow.features.queue_scope import CUTOFF_SETTING
from youmo_workflow.orchestration.backlog_control import SETTING, BacklogRun
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Publication, Task, WorkflowSetting

PENDING = ("pending", "retry_wait")


def clear_queue(
    runtime: Runtime, cutoff: datetime, plan: BatchPlan, authority: str
) -> dict[str, int]:
    if not authority.strip() or cutoff > runtime.tasks.clock():
        raise WorkflowError("QUEUE_CUT_INVALID", "清空队列需要明确授权与有效时间点。")
    boundary = stamp(cutoff)
    key = "queue-reset:" + hashlib.sha256(boundary.encode()).hexdigest()[:16]
    with runtime.factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if db.get(WorkflowSetting, key):
            raise WorkflowError("QUEUE_ALREADY_CLEARED", "此时间边界已处理，未重复清空。")
        old_cut = db.get(WorkflowSetting, CUTOFF_SETTING)
        if old_cut and parse_stamp(json.loads(old_cut.value_json)["after"]) >= cutoff:
            raise WorkflowError("QUEUE_CUT_INVALID", "不能回退已确认的素材时间边界。")
        require_idle(db)
        tasks = list(
            db.scalars(
                select(Task).where(
                    Task.kind.in_((*PRODUCER_KINDS, "publish")), Task.status.in_(PENDING)
                )
            )
        )
        publications = cancellable_publications(db)
        require_safe_upload_tasks(tasks, publications)
        batches = list(
            db.scalars(
                select(Batch).where(
                    Batch.id.in_({task.batch_id for task in tasks if task.batch_id})
                )
            )
        )
        saved = {
            name: db.get(WorkflowSetting, name)
            for name in (CUTOFF_SETTING, SETTING, "quantity_batch_plan")
        }
        audit = {
            "at": stamp(runtime.tasks.clock()),
            "cutoff": boundary,
            "authority": authority,
            "tasks": [task_snapshot(task) for task in tasks],
            "publications": [
                {"id": row.id, "status": row.status, "manifest_hash": row.manifest_hash}
                for row in publications
            ],
            "batches": [
                {
                    "id": row.id,
                    "status": row.status,
                    "error_code": row.error_code,
                    "error_message": row.error_message,
                }
                for row in batches
            ],
            "previous_settings": {
                name: row.value_json if row else None for name, row in saved.items()
            },
            "files_deleted": 0,
            "model_budgets_reset": False,
        }
        db.add(WorkflowSetting(id=key, revision=1, value_json=json.dumps(audit)))
        for task in tasks:
            task.status, task.updated_at = "cancelled", stamp(runtime.tasks.clock())
        for row in publications:
            row.status = "cancelled"
        for batch in batches:
            batch.status = "cancelled"
        frozen = saved[SETTING]
        if frozen:
            run = BacklogRun.model_validate_json(frozen.value_json)
            run.enabled, run.status, run.last_error = False, "cancelled", "USER_QUEUE_CLEARED"
            store_setting(db, SETTING, run.model_dump_json())
        store_setting(db, CUTOFF_SETTING, json.dumps({"after": boundary, "audit_key": key}))
        store_setting(db, "quantity_batch_plan", plan.model_dump_json())
        return {
            "cancelled_tasks": len(tasks),
            "cancelled_publications": len(publications),
            "cancelled_batches": len(batches),
            "files_deleted": 0,
        }


def require_idle(db: Session) -> None:
    if db.scalar(
        select(Task.id).where(
            Task.kind.in_((*PRODUCER_KINDS, "publish", "finalize_private")),
            Task.status == "running",
        )
    ) or db.scalar(select(Publication.id).where(Publication.status == "submitting")):
        raise WorkflowError("QUEUE_BUSY", "存在在途制作/投稿，等待结束后再清队列。")


def cancellable_publications(db: Session) -> list[Publication]:
    return [
        row
        for row in db.scalars(
            select(Publication).where(
                Publication.status.in_(["prepared", "confirmed", "queued"]),
                Publication.remote_id.is_(None),
            )
        )
        if not PublicationManifest.model_validate_json(row.manifest_json).test_only
    ]


def require_safe_upload_tasks(tasks: list[Task], publications: list[Publication]) -> None:
    identifiers = {row.id for row in publications}
    for task in tasks:
        if task.kind == "publish" and (
            FinalizePrivate.model_validate_json(task.input_json).publication_id not in identifiers
        ):
            raise WorkflowError("QUEUE_WRITE_UNRESOLVED", "待投任务不属于明确未投递稿件，未清空。")


def task_snapshot(task: Task) -> dict[str, str | int | None]:
    return {
        "id": task.id,
        "status": task.status,
        "attempts": task.attempts,
        "max_attempts": task.max_attempts,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "updated_at": task.updated_at,
        "input_sha256": hashlib.sha256(task.input_json.encode()).hexdigest(),
    }


def store_setting(db: Session, key: str, value: str) -> None:
    row = db.get(WorkflowSetting, key)
    if row:
        row.value_json, row.revision = value, row.revision + 1
    else:
        db.add(WorkflowSetting(id=key, value_json=value, revision=1))
