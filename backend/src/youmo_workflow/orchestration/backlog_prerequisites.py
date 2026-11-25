"""Keep ratio-change predecessor obligations across frozen-manifest hashes."""

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.features.production_buffer import PRODUCER_KINDS, producer_failed
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting


class RatioRecoveryAudit(BaseModel):
    resume_requires_repaired_production_batches: list[str] = Field(default_factory=list[str])


def require_repaired_predecessors(db: Session) -> None:
    for row in db.scalars(
        select(WorkflowSetting).where(WorkflowSetting.id.startswith("batch-ratio-change:"))
    ):
        audit = RatioRecoveryAudit.model_validate_json(row.value_json)
        for identifier in audit.resume_requires_repaired_production_batches:
            batch = db.get(Batch, identifier)
            if batch is None or producer_failed(db, batch):
                raise WorkflowError(
                    "BACKLOG_BATCH_FAILED", "比例变更前的制作批次仍未修复，未追加新批次。"
                )
            if db.scalar(
                select(Task.id).where(
                    Task.batch_id == identifier,
                    Task.kind.in_(PRODUCER_KINDS),
                    Task.status.in_(["pending", "running", "retry_wait"]),
                )
            ):
                raise WorkflowError(
                    "BACKLOG_PREDECESSOR_ACTIVE", "比例变更前的制作批次仍在恢复中。"
                )
