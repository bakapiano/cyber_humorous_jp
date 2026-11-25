"""Durable dependency holds, never a global gate inferred from a failed video."""

import re
from datetime import datetime
from typing import Literal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.pipeline import MODEL_KINDS, PREPARATION_KINDS, PRODUCTION_KINDS
from youmo_workflow.domain.time import stamp
from youmo_workflow.storage.models import Task, WorkflowSetting

Scope = Literal["model", "download", "storage", "publisher"]
PREFIX = "pipeline_hold:"
SCOPES: dict[Scope, tuple[str, ...]] = {
    "model": MODEL_KINDS,
    "download": PREPARATION_KINDS,
    "storage": PRODUCTION_KINDS,
    "publisher": ("publish",),
}


class DependencyHold(Contract):
    scope: Scope
    active: bool = True
    task_id: str
    error_code: str
    changed_at: str
    reason: str = ""


def failure_scope(error: WorkflowError) -> Scope | None:
    if error.code in {"DISK_SPACE_LOW", "BACKLOG_DISK_LOW"}:
        return "storage"
    if error.code in {"SELECTION_KEY_MISSING", "SELECTION_KEY_INVALID"}:
        return "model"
    if error.code in {
        "MODEL_HTTP_ERROR",
        "MODEL_HTTP_NON_RETRYABLE",
        "MODEL_HTTP_RETRIES_EXHAUSTED",
    }:
        if re.search(r"HTTP\s*(401|402|403|429)\b", error.message):
            return "model"
    if error.code in {
        "SOURCE_LOGIN_REQUIRED",
        "SOURCE_SESSION_AUTH_FAILED",
        "SOURCE_SESSION_CONFIG_ERROR",
        "SOURCE_SESSION_INVALID",
        "SOURCE_SESSION_RETRIES_EXHAUSTED",
        "NODE_MISSING",
    }:
        return "download"
    if error.code in {
        "BILI_NOT_CONFIGURED",
        "BILI_LOGIN_REQUIRED",
        "BILI_APP_LOGIN_REQUIRED",
        "BILI_CAPTCHA_REQUIRED",
    }:
        return "publisher"
    return None


def hold_dependency(db: Session, task: Task, error: WorkflowError, at: datetime) -> None:
    scope = failure_scope(error)
    if scope is None:
        return
    value = DependencyHold(
        scope=scope, task_id=task.id, error_code=error.code, changed_at=stamp(at)
    )
    row = db.get(WorkflowSetting, PREFIX + scope)
    if row:
        if DependencyHold.model_validate_json(row.value_json).active:
            return
        row.value_json, row.revision = value.model_dump_json(), row.revision + 1
    else:
        db.add(WorkflowSetting(id=PREFIX + scope, revision=1, value_json=value.model_dump_json()))


def active_holds(db: Session) -> list[DependencyHold]:
    rows = db.scalars(
        select(WorkflowSetting).where(WorkflowSetting.id.in_([PREFIX + name for name in SCOPES]))
    )
    return [
        state
        for row in rows
        if (state := DependencyHold.model_validate_json(row.value_json)).active
    ]


def blocked_kinds(db: Session) -> set[str]:
    return {kind for hold in active_holds(db) for kind in SCOPES[hold.scope]}


def release_hold(db: Session, scope: Scope, reason: str, at: datetime) -> None:
    if not reason.strip():
        raise WorkflowError("HOLD_REASON_REQUIRED", "恢复阶段前必须记录已解决故障的依据。")
    row = db.get(WorkflowSetting, PREFIX + scope)
    if row is None:
        return
    state = DependencyHold.model_validate_json(row.value_json)
    if not state.active:
        return
    db.add(
        WorkflowSetting(id=f"{PREFIX}history:{uuid4().hex}", revision=1, value_json=row.value_json)
    )
    state.active, state.changed_at, state.reason = False, stamp(at), reason
    row.value_json, row.revision = state.model_dump_json(), row.revision + 1
