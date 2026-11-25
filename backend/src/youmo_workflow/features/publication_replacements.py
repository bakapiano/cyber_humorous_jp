"""Audited replacement lineage; superseded tasks never count as completed uploads."""

import hashlib
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import SelectedContent
from youmo_workflow.domain.replacements import Replacement
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_task_view import REPLACEMENT_PREFIX
from youmo_workflow.features.tasks import add_task
from youmo_workflow.storage.models import Publication, Task, WorkflowSetting


def stage_replacement(
    factory: sessionmaker[Session], publication_id: str, version: str, at: datetime
) -> Replacement:
    with factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        old = db.get(Publication, publication_id)
        if (
            old is None
            or old.status != "withdrawn"
            or not old.remote_id
            or old.confirmed_hash != old.manifest_hash
        ):
            raise WorkflowError("WITHDRAWAL_REQUIRED", "旧稿尚未确认下架，不能创建替代稿。")
        prior = db.get(WorkflowSetting, REPLACEMENT_PREFIX + old.id)
        if prior:
            record = Replacement.model_validate_json(prior.value_json)
            if record.version != version or record.old_manifest_hash != old.manifest_hash:
                raise WorkflowError("REPLACEMENT_CHANGED", "已有不同的重做版本，不覆盖或重复排队。")
            return record
        source = db.scalar(
            select(Task).where(
                Task.kind == "prepare_video",
                Task.status == "succeeded",
                func.json_extract(Task.output_json, "$.content_id") == old.content_id,
            )
        )
        if source is None or not source.batch_id or not source.output_json:
            raise WorkflowError("REPLACEMENT_SOURCE_MISSING", "没有匹配的已完成原片准备记录。")
        original = SelectedContent.model_validate_json(source.output_json)
        suffix = hashlib.sha256(version.encode()).hexdigest()[:10]
        content = original.model_copy(update={"content_id": f"{original.content_id}-r-{suffix}"})
        task = add_task(db, "packaging", f"replacement-package:{old.id}:{version}", stamp(at))
        task.batch_id, task.input_json, task.max_attempts = (
            source.batch_id,
            content.model_dump_json(),
            1,
        )
        keys = [f"batch-publish:{old.id}", f"finalize:{old.id}:{old.manifest_hash}"]
        superseded = list(db.scalars(select(Task).where(Task.unique_key.in_(keys))))
        if not superseded or any(row.status == "running" for row in superseded):
            raise WorkflowError("REPLACEMENT_BUSY", "旧稿任务不存在或仍在执行，未重做。")
        record = Replacement(
            old_publication_id=old.id,
            old_manifest_hash=old.manifest_hash,
            old_remote_id=old.remote_id,
            old_content_id=old.content_id,
            new_content_id=content.content_id,
            batch_id=source.batch_id,
            packaging_task_id=task.id,
            superseded_task_ids=[row.id for row in superseded],
            previous_task_states={row.id: row.status for row in superseded},
            authorized_at=stamp(at),
            version=version,
        )
        db.add(
            WorkflowSetting(
                id=REPLACEMENT_PREFIX + old.id, revision=1, value_json=record.model_dump_json()
            )
        )
        for row in superseded:
            row.status, row.updated_at = "superseded", stamp(at)
        return record
