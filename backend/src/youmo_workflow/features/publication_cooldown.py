from datetime import datetime, timedelta

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.time import parse_stamp, stamp
from youmo_workflow.features.remote_hold import require_remote_access
from youmo_workflow.storage.models import Publication, WorkflowSetting

SETTING = "bilibili_upload_cooldown"
RATE_LIMIT_ERRORS = frozenset({"BILI_PREUPLOAD_RATE_LIMIT", "BILI_SUBMIT_RATE_LIMIT"})
BACKOFF_MINUTES = (5, 10, 20, 30, 60)
MINIMUM_GAP = timedelta(minutes=30, seconds=30)


class UploadCooldown(Contract):
    until: str
    consecutive_rejections: int = Field(ge=0)
    last_code: str = "601"
    next_submit_at: str | None = None


def active_until(db: Session, at: datetime) -> str | None:
    row = db.get(WorkflowSetting, SETTING)
    if row is None:
        return None
    state = UploadCooldown.model_validate_json(row.value_json)
    candidates = [state.until, state.next_submit_at]
    future = [value for value in candidates if value and parse_stamp(value) > at]
    return max(future, key=parse_stamp) if future else None


def require_upload_slot(db: Session, publication_id: str, at: datetime) -> None:
    require_remote_access(db)
    if active_until(db, at):
        raise WorkflowError(
            "BILI_UPLOAD_COOLDOWN", "投稿间隔或限流冷却尚未结束，未发送新请求。", retryable=True
        )
    if db.scalar(
        select(Publication.id).where(
            Publication.status == "submitting", Publication.id != publication_id
        )
    ):
        raise WorkflowError(
            "BILI_UPLOAD_BUSY", "已有上传正在执行，当前稿件稍后再试。", retryable=True
        )


def store_state(db: Session, state: UploadCooldown) -> None:
    row = db.get(WorkflowSetting, SETTING)
    if row is None:
        db.add(WorkflowSetting(id=SETTING, value_json=state.model_dump_json(), revision=1))
    else:
        row.value_json, row.revision = state.model_dump_json(), row.revision + 1


def current_state(db: Session, at: datetime) -> UploadCooldown:
    row = db.get(WorkflowSetting, SETTING)
    return (
        UploadCooldown.model_validate_json(row.value_json)
        if row
        else UploadCooldown(until=stamp(at), consecutive_rejections=0)
    )


def pace_attempt(db: Session, at: datetime) -> None:
    state = current_state(db, at)
    proposed = stamp(at + MINIMUM_GAP)
    state.next_submit_at = max(state.next_submit_at or proposed, proposed, key=parse_stamp)
    store_state(db, state)


def record_rejection(db: Session, at: datetime, code: str = "601") -> str:
    state = current_state(db, at)
    minutes = BACKOFF_MINUTES[min(state.consecutive_rejections, len(BACKOFF_MINUTES) - 1)]
    until = stamp(at + timedelta(minutes=minutes))
    state.until, state.last_code = until, code
    state.consecutive_rejections += 1
    state.next_submit_at = stamp(at + MINIMUM_GAP)
    store_state(db, state)
    return until


def clear_rejections(db: Session, at: datetime) -> None:
    store_state(
        db,
        UploadCooldown(
            until=stamp(at), consecutive_rejections=0, next_submit_at=stamp(at + MINIMUM_GAP)
        ),
    )
