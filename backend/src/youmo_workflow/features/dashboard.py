from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.models import Dashboard, Policy
from youmo_workflow.domain.time import now_utc
from youmo_workflow.features.batches import eligible_query
from youmo_workflow.storage.models import Batch, Source, Task, Video


def dashboard(
    factory: sessionmaker[Session],
    policy: Policy,
    switches: tuple[bool, bool],
    clock: Callable[[], datetime] = now_utc,
) -> Dashboard:
    with factory() as db:
        return Dashboard(
            sources=db.scalar(select(func.count()).select_from(Source)) or 0,
            enabled_sources=db.scalar(
                select(func.count()).select_from(Source).where(Source.enabled)
            )
            or 0,
            videos=db.scalar(select(func.count()).select_from(Video)) or 0,
            eligible_videos=db.scalar(
                select(func.count()).select_from(eligible_query(clock(), policy).subquery())
            )
            or 0,
            batches=db.scalar(select(func.count()).select_from(Batch)) or 0,
            failed_tasks=db.scalar(
                select(func.count()).select_from(Task).where(Task.status == "failed")
            )
            or 0,
            policy=policy,
            automation_enabled=switches[0],
            publication_enabled=switches[1],
        )
