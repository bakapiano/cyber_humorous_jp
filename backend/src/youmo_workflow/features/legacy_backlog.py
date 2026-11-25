from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.storage.models import Batch, Task


def release_unstarted_batches(factory: sessionmaker[Session]) -> list[str]:
    """Explicit rollout step; never release an attempted real model batch."""
    released: list[str] = []
    with factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        batches = db.scalars(
            select(Batch).where(
                Batch.status == "failed",
                Batch.error_code == "SELECTION_NOT_IMPLEMENTED",
                Batch.execution_json.is_(None),
                Batch.reservations_released.is_(False),
            )
        )
        for batch in batches:
            tasks = list(db.scalars(select(Task).where(Task.batch_id == batch.id)))
            untouched = tasks and all(
                task.kind == "selection"
                and task.status == "failed"
                and task.error_code == "SELECTION_NOT_IMPLEMENTED"
                and task.output_json is None
                for task in tasks
            )
            if untouched:
                batch.reservations_released = True
                released.append(batch.id)
    return released
