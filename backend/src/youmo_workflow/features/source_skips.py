"""Audited source skips, separate from review refusals and upload success."""

from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.media_limits import MAX_SOURCE_SECONDS
from youmo_workflow.domain.models import VideoDTO
from youmo_workflow.domain.source_skip import (
    DOWNLOAD_SKIP_ERRORS,
    DownloadSkip,
    SourceDownloadEvidence,
    SourceDurationEvidence,
    SourceSkip,
)
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.review_skips import signature
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting

PREFIX = "source_skip:"
ERRORS = {"SOURCE_TOO_LONG"} | DOWNLOAD_SKIP_ERRORS
RECORD = TypeAdapter(SourceSkip | DownloadSkip)


def eligible(db: Session, task: Task) -> bool:
    batch = db.get(Batch, task.batch_id) if task.batch_id else None
    return bool(
        batch
        and batch.status != "cancelled"
        and batch.execution_json
        and BatchExecution.model_validate_json(batch.execution_json).producer_consumer
        and task.kind == "prepare_video"
        and task.error_code in ERRORS
    )


def mark_source_skip(
    db: Session, task: Task, evidence: SourceDurationEvidence | SourceDownloadEvidence, at: datetime
) -> None:
    if task.status != "failed" or not eligible(db, task):
        return
    video = VideoDTO.model_validate_json(task.input_json)
    if video.video_id != evidence.video_id or not evidence_matches(evidence, task):
        raise WorkflowError("SOURCE_SKIP_UNVERIFIED", "素材失败证据与当前任务不匹配。")
    if db.get(WorkflowSetting, PREFIX + task.id):
        raise WorkflowError("SOURCE_SKIP_EXISTS", "已有素材跳过记录，未覆盖原账本。")
    record = RECORD.validate_python(
        {
            **evidence.model_dump(),
            "task_id": task.id,
            "batch_id": task.batch_id,
            "input_sha256": signature(task.input_json),
            "output_sha256": signature(task.output_json),
            "attempts": task.attempts,
            "error_code": task.error_code,
            "authorized_at": stamp(at),
        }
    )
    db.add(WorkflowSetting(id=PREFIX + task.id, revision=1, value_json=record.model_dump_json()))
    task.status = "skipped"


def verified_source_skips(db: Session, batch_id: str, tasks: list[Task]) -> set[str]:
    skipped: set[str] = set()
    for task in tasks:
        row = db.get(WorkflowSetting, PREFIX + task.id)
        if row is None or not eligible(db, task):
            raise WorkflowError("SOURCE_SKIP_UNVERIFIED", "素材跳过缺少可核验的失败证据。")
        record = RECORD.validate_json(row.value_json)
        video = VideoDTO.model_validate_json(task.input_json)
        if (
            task.status != "skipped"
            or record.task_id != task.id
            or record.batch_id != batch_id
            or task.batch_id != batch_id
            or record.video_id != video.video_id
            or not evidence_matches(record, task)
            or record.error_code != task.error_code
            or record.input_sha256 != signature(task.input_json)
            or record.output_sha256 != signature(task.output_json)
            or record.attempts != task.attempts
            or record.video_id in skipped
        ):
            raise WorkflowError("SOURCE_SKIP_UNVERIFIED", "素材跳过记录与原始任务不一致。")
        skipped.add(record.video_id)
    return skipped


def evidence_matches(evidence: SourceDurationEvidence | SourceDownloadEvidence, task: Task) -> bool:
    if isinstance(evidence, SourceDurationEvidence):
        return evidence.limit_seconds == MAX_SOURCE_SECONDS
    return evidence.error_code == task.error_code and evidence.message == task.error_message
