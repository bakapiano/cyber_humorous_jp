"""Explicit, hash-bound review skips; network/unknown/format errors are never skips."""

import hashlib
from datetime import datetime

from sqlalchemy.orm import Session

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.review_skip import ReviewSkip
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.review_skip_scope import authorized_review_skip
from youmo_workflow.features.review_skip_subject import review_subject
from youmo_workflow.storage.models import Task, WorkflowSetting

PREFIX = "review_skip:"


def signature(value: str | None) -> str:
    return hashlib.sha256((value or "").encode()).hexdigest()


def mark_review_skip(db: Session, task: Task, at: datetime) -> None:
    if task.batch_id is None or task.status != "failed":
        return
    assert task.batch_id and task.error_code
    subject = review_subject(db, task)
    if subject is None or not authorized_review_skip(db, task, subject[1]):
        return
    record = ReviewSkip(
        task_id=task.id,
        batch_id=task.batch_id,
        video_id=subject[0],
        content_id=subject[1],
        error_code=task.error_code,
        input_sha256=signature(task.input_json),
        output_sha256=signature(task.output_json),
        attempts=task.attempts,
        authorized_at=stamp(at),
    )
    if db.get(WorkflowSetting, PREFIX + task.id):
        raise WorkflowError("REVIEW_SKIP_EXISTS", "已有审核跳过记录，不重置任务或覆盖依据。")
    db.add(WorkflowSetting(id=PREFIX + task.id, revision=1, value_json=record.model_dump_json()))
    task.status = "skipped"


def verified_review_skips(db: Session, batch_id: str, tasks: list[Task]) -> set[str]:
    skipped: set[str] = set()
    for task in tasks:
        if task.status != "skipped":
            continue
        row = db.get(WorkflowSetting, PREFIX + task.id)
        if row is None:
            raise WorkflowError("REVIEW_SKIP_UNVERIFIED", "跳过任务缺少本批明确授权及审查依据。")
        record = ReviewSkip.model_validate_json(row.value_json)
        subject = review_subject(db, task)
        if subject is None or not authorized_review_skip(db, task, subject[1]):
            raise WorkflowError("REVIEW_SKIP_UNVERIFIED", "该任务类型没有审核跳过依据。")
        if (
            record.task_id != task.id
            or record.batch_id != batch_id
            or record.error_code != task.error_code
            or record.input_sha256 != signature(task.input_json)
            or record.output_sha256 != signature(task.output_json)
            or record.attempts != task.attempts
            or record.video_id != subject[0]
            or record.content_id != subject[1]
            or record.publication_succeeded
            or record.video_id in skipped
        ):
            raise WorkflowError("REVIEW_SKIP_UNVERIFIED", "审核跳过记录与原始任务不一致。")
        skipped.add(record.video_id)
    return skipped
