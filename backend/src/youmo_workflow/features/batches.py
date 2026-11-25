from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import BatchDetail, BatchDTO, Policy, VideoDTO
from youmo_workflow.domain.time import BUSINESS_ZONE, now_utc, stamp
from youmo_workflow.features.initial_selection import freeze_initial_task
from youmo_workflow.features.queue_scope import cutoff_stamp
from youmo_workflow.features.sources import video_dto
from youmo_workflow.features.tasks import add_task
from youmo_workflow.storage.models import Batch, BatchItem, Publication, Task, Video


def batch_dto(row: Batch) -> BatchDTO:
    execution = (
        BatchExecution.model_validate_json(row.execution_json) if row.execution_json else None
    )
    return BatchDTO(
        id=row.id,
        local_date=row.local_date,
        status=row.status,
        snapshot_at=row.snapshot_at,
        candidate_count=row.candidate_count,
        policy=Policy.model_validate_json(row.policy_json),
        error_code=row.error_code,
        error_message=row.error_message,
        trigger_mode=execution.trigger if execution else "legacy",
        publications_per_batch=execution.publications_per_batch if execution else None,
    )


def eligible_query(at: datetime, policy: Policy):
    cutoff = cutoff_stamp()
    reserved = select(BatchItem.video_id).join(Batch).where(Batch.reservations_released.is_(False))
    produced = (
        select(Publication.id)
        .where(
            func.json_extract(Publication.manifest_json, "$.source_url")
            == "https://www.youtube.com/watch?v=" + Video.id
        )
        .exists()
    )
    single = (
        select(Task.id)
        .where(
            Task.kind == "prepare_video",
            func.json_extract(Task.input_json, "$.video_id") == Video.id,
        )
        .exists()
    )
    return (
        select(Video)
        .where(
            Video.screened.is_(False),
            or_(cutoff.is_(None), Video.published_at > cutoff),
            Video.id.not_in(reserved),
            ~produced,
            ~single,
            Video.published_at >= stamp(at - timedelta(hours=policy.candidate_window_hours)),
            Video.published_at <= stamp(at),
        )
        .order_by(Video.published_at.desc(), Video.id)
    )


class BatchService:
    def __init__(self, factory: sessionmaker[Session], clock: Callable[[], datetime] = now_utc):
        self.factory = factory
        self.clock = clock

    def create(self, policy: Policy) -> BatchDTO:
        at = self.clock()
        day = at.astimezone(BUSINESS_ZONE).date().isoformat()
        with self.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            previous = db.scalar(
                select(Batch).where(Batch.local_date == day, Batch.execution_json.is_(None))
            )
            if previous:
                return batch_dto(previous)
            active = db.scalar(select(Batch).where(Batch.status.in_(["queued", "running"])))
            if active:
                raise WorkflowError("BATCH_ALREADY_ACTIVE", "上一批仍在执行，本次不创建重叠批次。")
            rows = list(db.scalars(eligible_query(at, policy)))
            row = Batch(
                id=uuid4().hex,
                local_date=day,
                snapshot_at=stamp(at),
                policy_json=policy.model_dump_json(),
                candidate_count=len(rows),
                status="queued" if rows else "completed",
            )
            db.add(row)
            db.flush()
            for video in rows:
                db.add(
                    BatchItem(
                        batch_id=row.id,
                        video_id=video.id,
                        snapshot_json=video_dto(video).model_dump_json(),
                    )
                )
            if rows:
                if policy.initial_selection_version != "legacy":
                    freeze_initial_task(
                        db,
                        row,
                        [video_dto(video) for video in rows],
                        stamp(at),
                        policy.initial_selection_version,
                    )
                else:
                    task = add_task(db, "selection", f"selection:{row.id}", stamp(at))
                    task.batch_id = row.id
            return batch_dto(row)

    def list_batches(self) -> list[BatchDTO]:
        with self.factory() as db:
            return [
                batch_dto(row)
                for row in db.scalars(
                    select(Batch).order_by(Batch.snapshot_at.desc(), Batch.id).limit(30)
                )
            ]

    def detail(self, batch_id: str) -> BatchDetail:
        with self.factory() as db:
            row = db.get(Batch, batch_id)
            if row is None:
                raise WorkflowError("BATCH_NOT_FOUND", "批次不存在。")
            snapshots = db.scalars(
                select(BatchItem.snapshot_json).where(BatchItem.batch_id == batch_id)
            )
            return BatchDetail(
                **batch_dto(row).model_dump(),
                items=[VideoDTO.model_validate_json(item) for item in snapshots],
            )

    def set_result(self, batch_id: str, status: str, error: WorkflowError | None = None) -> None:
        with self.factory.begin() as db:
            row = db.get(Batch, batch_id)
            if row:
                row.status = status
                row.error_code = error.code if error else None
                row.error_message = error.message if error else None
