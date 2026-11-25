from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.batch_plan import BatchExecution, BatchTrigger
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import BatchDTO, Policy, VideoDTO
from youmo_workflow.domain.time import BUSINESS_ZONE, now_utc, stamp
from youmo_workflow.features.batch_plan import automatic_blocked, read_plan
from youmo_workflow.features.batches import batch_dto, eligible_query
from youmo_workflow.features.initial_selection import freeze_initial_task
from youmo_workflow.features.production_buffer import (
    buffer_available,
    require_buffer_capacity,
)
from youmo_workflow.features.sources import video_dto
from youmo_workflow.storage.models import Batch, BatchItem, Video


class BatchTriggerService:
    def __init__(
        self,
        factory: sessionmaker[Session],
        policy: Policy,
        clock: Callable[[], datetime] = now_utc,
        publication_ready: Callable[[], bool] | None = None,
    ) -> None:
        self.factory, self.policy, self.clock = factory, policy, clock
        self.publication_ready = publication_ready

    def trigger(self, request: BatchTrigger | None = None) -> BatchDTO | None:
        with self.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            key = f"manual:{request.request_id}" if request else f"automatic:{uuid4().hex}"
            previous = db.scalar(select(Batch).where(Batch.run_key == key))
            if previous:
                return batch_dto(previous)
            plan, revision = read_plan(db)
            if request and request.revision != revision:
                raise WorkflowError("PLAN_CHANGED", "配置已改变，请按最新批量与投稿数量重新确认。")
            if request is None and (not plan.auto_enabled or automatic_blocked(db)):
                return None
            if not buffer_available(db, plan.publications_per_batch, manual=request is not None):
                return None
            rows = list(
                db.scalars(eligible_query(self.clock(), self.policy).limit(plan.batch_size))
            )
            if len(rows) < plan.batch_size:
                if request is None:
                    return None
                raise WorkflowError(
                    "BATCH_NOT_FULL",
                    f"素材不足一批：{len(rows)}/{plan.batch_size}；未调用模型或投稿。",
                )
            execution = BatchExecution(
                batch_size=plan.batch_size,
                publications_per_batch=plan.publications_per_batch,
                plan_revision=revision,
                trigger="manual" if request else "automatic",
                publication_mode=plan.publication_mode,
                editing_revision="v005",
                producer_consumer=True,
                skip_review_rejections=True,
            )
            return self.freeze(db, rows, key, execution)

    def freeze(
        self, db: Session, rows: list[Video], key: str, execution: BatchExecution
    ) -> BatchDTO:
        return self.freeze_items(db, [video_dto(row) for row in rows], key, execution)

    def freeze_items(
        self,
        db: Session,
        videos: list[VideoDTO],
        key: str,
        execution: BatchExecution,
        *,
        snapshot_at: datetime | None = None,
    ) -> BatchDTO:
        at = self.clock()
        if execution.producer_consumer:
            require_buffer_capacity(db, execution.publications_per_batch)
        policy = Policy.model_validate(
            {
                **self.policy.model_dump(),
                "initial_selection_version": "v008",
                "candidate_limit": 30,
                "max_approved": 30,
            }
        )
        batch = Batch(
            id=uuid4().hex,
            local_date=at.astimezone(BUSINESS_ZONE).date().isoformat(),
            snapshot_at=stamp(snapshot_at or at),
            policy_json=policy.model_dump_json(),
            candidate_count=len(videos),
            status="queued",
            run_key=key,
            execution_json=execution.model_dump_json(),
        )
        db.add(batch)
        db.flush()
        for video in videos:
            db.add(
                BatchItem(
                    batch_id=batch.id,
                    video_id=video.video_id,
                    snapshot_json=video.model_dump_json(),
                )
            )
        freeze_initial_task(db, batch, videos, stamp(at), "v008")
        return batch_dto(batch)
