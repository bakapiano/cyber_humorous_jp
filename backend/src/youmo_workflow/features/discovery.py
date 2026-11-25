import json
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Policy, TaskDTO
from youmo_workflow.domain.time import BUSINESS_ZONE, now_utc, stamp
from youmo_workflow.features.tasks import add_task, task_dto
from youmo_workflow.storage.models import Source


def enqueue_discovery(
    factory: sessionmaker[Session],
    source_id: str | None = None,
    clock: Callable[[], datetime] = now_utc,
    policy: Policy | None = None,
) -> list[TaskDTO]:
    at = clock()
    current_policy = policy or Policy()
    with factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return queue_discovery(db, source_id, current_policy, at)


def queue_discovery(
    db: Session, source_id: str | None, policy: Policy, at: datetime
) -> list[TaskDTO]:
    bucket = int(at.timestamp()) // (policy.channel_poll_minutes * 60)
    query = select(Source).where(Source.enabled)
    if source_id:
        query = query.where(Source.id == source_id)
    sources = list(db.scalars(query))
    if not sources:
        raise WorkflowError("NO_ENABLED_SOURCES", "没有已启用的来源。")
    result: list[TaskDTO] = []
    for source in sources:
        task = add_task(
            db, "sync_source", f"sync:{source.id}:{policy.channel_poll_minutes}:{bucket}", stamp(at)
        )
        task.input_json = json.dumps(
            {"source_id": source.id, "window_hours": policy.candidate_window_hours}
        )
        result.append(task_dto(task))
    return result


def enqueue_home(
    factory: sessionmaker[Session], clock: Callable[[], datetime] = now_utc
) -> TaskDTO:
    at = clock()
    day = at.astimezone(BUSINESS_ZONE).date().isoformat()
    with factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        return task_dto(add_task(db, "sync_home", f"home:{day}", stamp(at)))
