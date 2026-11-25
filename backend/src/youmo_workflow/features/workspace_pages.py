from sqlalchemy import Integer, func, literal_column, select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.workspace_views import BatchPage, PublicationPage, TaskPage
from youmo_workflow.features.batches import batch_dto
from youmo_workflow.features.publications import publication_dto
from youmo_workflow.features.tasks import task_dto
from youmo_workflow.storage.models import Batch, Publication, Task


def bounded_offset(total: int, limit: int, offset: int) -> int:
    return min(offset, max(0, (total - 1) // limit) * limit)


def batch_page(factory: sessionmaker[Session], limit: int, offset: int) -> BatchPage:
    with factory() as db:
        total = db.scalar(select(func.count()).select_from(Batch)) or 0
        offset = bounded_offset(total, limit, offset)
        # Frozen cohorts share snapshot_at; insertion order represents batch creation.
        rows = db.scalars(
            select(Batch)
            .order_by(literal_column("production_batches.rowid", Integer).desc())
            .offset(offset)
            .limit(limit)
        )
        return BatchPage(
            total=total, offset=offset, limit=limit, items=[batch_dto(row) for row in rows]
        )


def task_page(factory: sessionmaker[Session], limit: int, offset: int) -> TaskPage:
    with factory() as db:
        total = db.scalar(select(func.count()).select_from(Task)) or 0
        offset = bounded_offset(total, limit, offset)
        rows = db.scalars(
            select(Task)
            .order_by(Task.created_at.desc(), Task.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return TaskPage(
            total=total, offset=offset, limit=limit, items=[task_dto(row) for row in rows]
        )


def publication_page(factory: sessionmaker[Session], limit: int, offset: int) -> PublicationPage:
    with factory() as db:
        total = db.scalar(select(func.count()).select_from(Publication)) or 0
        offset = bounded_offset(total, limit, offset)
        rows = db.scalars(
            select(Publication)
            .order_by(Publication.created_at.desc(), Publication.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return PublicationPage(
            total=total, offset=offset, limit=limit, items=[publication_dto(row) for row in rows]
        )
