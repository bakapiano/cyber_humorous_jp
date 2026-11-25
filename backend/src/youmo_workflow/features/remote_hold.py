"""Persisted platform-access hold: local production may continue, remote jobs cannot."""

from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.time import stamp
from youmo_workflow.storage.models import WorkflowSetting

SETTING = "bilibili_remote_access_hold"


class RemoteAccessHold(Contract):
    active: bool
    reason: str
    changed_at: str


def remote_held(db: Session) -> bool:
    row = db.get(WorkflowSetting, SETTING)
    return bool(row and RemoteAccessHold.model_validate_json(row.value_json).active)


def require_remote_access(db: Session) -> None:
    if remote_held(db):
        raise WorkflowError(
            "REMOTE_ACCESS_BLOCKED",
            "平台访问待恢复；保留本地产物，未发送投稿请求。",
            retryable=True,
        )


def set_remote_hold(
    factory: sessionmaker[Session], active: bool, reason: str, at: datetime
) -> None:
    with factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = db.get(WorkflowSetting, SETTING)
        state = RemoteAccessHold(active=active, reason=reason, changed_at=stamp(at))
        if row:
            db.add(
                WorkflowSetting(
                    id=f"{SETTING}:history:{row.revision}", revision=1, value_json=row.value_json
                )
            )
            row.value_json, row.revision = state.model_dump_json(), row.revision + 1
        else:
            db.add(WorkflowSetting(id=SETTING, revision=1, value_json=state.model_dump_json()))
