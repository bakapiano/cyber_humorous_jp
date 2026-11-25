import hashlib
from typing import Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting


class PublicAuthorization(Contract):
    batch_id: str
    execution_sha256: str
    publication_mode: Literal["public_reprint"] = "public_reprint"
    authorized_at: str
    reason: str = Field(min_length=1)


def visibility(db: Session, batch: Batch) -> Literal["private", "public"]:
    if not batch.execution_json:
        raise WorkflowError("BATCH_AUTHORIZATION_REQUIRED", "批次没有执行授权。")
    execution = BatchExecution.model_validate_json(batch.execution_json)
    grant = db.get(WorkflowSetting, f"public_authorization:{batch.id}")
    if grant:
        authorized = PublicAuthorization.model_validate_json(grant.value_json)
        if (
            authorized.batch_id != batch.id
            or authorized.execution_sha256
            != hashlib.sha256(batch.execution_json.encode()).hexdigest()
        ):
            raise WorkflowError("CONFIRMATION_STALE", "补充公开授权与原始批次快照不匹配。")
        return "public"
    return "public" if execution.publication_mode == "public_reprint" else "private"


def authorize_unsubmitted_public(
    factory: sessionmaker[Session], batch_id: str, reason: str
) -> None:
    """Explicit operator authorization only; never called by scheduling or retries."""
    with factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        batch = db.get(Batch, batch_id)
        if batch is None or not batch.execution_json:
            raise WorkflowError("BATCH_AUTHORIZATION_REQUIRED", "批次没有执行授权。")
        key = f"public_authorization:{batch_id}"
        if db.get(WorkflowSetting, key):
            visibility(db, batch)
            return
        if db.scalar(
            select(Task.id).where(
                Task.batch_id == batch_id, Task.kind.in_(["publish", "finalize_private"])
            )
        ):
            raise WorkflowError(
                "PUBLICATION_IMMUTABLE", "批次已有绑定投稿任务，不自动更改其可见性。"
            )
        authorization = PublicAuthorization(
            batch_id=batch_id,
            execution_sha256=hashlib.sha256(batch.execution_json.encode()).hexdigest(),
            authorized_at=stamp(now_utc()),
            reason=reason,
        )
        db.add(WorkflowSetting(id=key, revision=1, value_json=authorization.model_dump_json()))
