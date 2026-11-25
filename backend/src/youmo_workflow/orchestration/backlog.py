"""Explicit goal-driven consumption of a frozen cohort; failures remain unresolved."""

import hashlib
import shutil
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youmo_workflow.domain.batch_plan import BatchExecution, BatchPlan
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import BatchDTO, Contract, Policy, VideoDTO
from youmo_workflow.domain.time import parse_stamp
from youmo_workflow.features.batch_plan import read_plan
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.production_buffer import active_producer, require_buffer_capacity
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import (
    Batch,
    BatchItem,
    Publication,
    Task,
    Video,
    WorkflowSetting,
)


class BacklogSnapshot(Contract):
    captured_at: str
    plan: BatchPlan
    plan_revision: int
    policy: Policy
    items: list[VideoDTO] = Field(min_length=50, max_length=100000)
    existing_batches: list[dict[str, JsonValue]]
    rule: str


class BacklogCursor(Contract):
    snapshot_sha256: str
    batch_ids: list[str] = Field(default_factory=list[str])
    authority: str


class BacklogDrain:
    def __init__(self, runtime: Runtime, path: Path, *, skip_review_rejections: bool = False):
        self.runtime = runtime
        self.skip_review_rejections = skip_review_rejections
        resolved = path.resolve()
        if not resolved.is_relative_to((runtime.settings.root / "data/backlog").resolve()):
            raise WorkflowError("BACKLOG_PATH_DENIED", "积压清单不在本项目的冻结目录中。")
        raw = resolved.read_bytes()
        if len(raw) > 20 * 1024**2:
            raise WorkflowError("BACKLOG_TOO_LARGE", "积压清单超过读取上限。")
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.snapshot = BacklogSnapshot.model_validate_json(raw)
        if len({item.video_id for item in self.snapshot.items}) != len(self.snapshot.items):
            raise WorkflowError("BACKLOG_DUPLICATE", "冻结清单含重复视频，未创建批次。")
        self.key = "backlog_cursor:" + self.sha256

    def enqueue_next(self, confirm_execution: Literal[True]) -> BatchDTO | None:
        if confirm_execution is not True:
            raise WorkflowError("EXECUTION_CONFIRMATION_REQUIRED", "积压执行需要明确授权。")
        if shutil.disk_usage(self.runtime.settings.root).free < 20 * 1024**3:
            raise WorkflowError(
                "BACKLOG_DISK_LOW", "剩余空间不足20GB，保留失败视频并停止新增制作。"
            )
        if not self.runtime.settings.bilibili_ready():
            raise WorkflowError("BILI_LOGIN_REQUIRED", "发布账号未就绪，未启动新的模型请求。")
        with self.runtime.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            plan, revision = read_plan(db)
            expected = self.snapshot.plan
            if (plan.batch_size, plan.publications_per_batch, plan.publication_mode) != (
                expected.batch_size,
                expected.publications_per_batch,
                expected.publication_mode,
            ):
                raise WorkflowError(
                    "BACKLOG_PLAN_CHANGED", "处理比例或可见性已变，不能静默改变冻结目标。"
                )
            saved = db.get(WorkflowSetting, self.key)
            cursor = (
                BacklogCursor.model_validate_json(saved.value_json)
                if saved
                else BacklogCursor(
                    snapshot_sha256=self.sha256,
                    authority="用户2026-11-07明确授权完成所有积压完整批次，不逐批确认；失败仍需解决。",
                )
            )
            if cursor.snapshot_sha256 != self.sha256:
                raise WorkflowError("BACKLOG_CHANGED", "积压游标与清单哈希不匹配。")
            validate_cursor(db, cursor)
            if active_producer(db):
                return None
            require_buffer_capacity(db, plan.publications_per_batch)
            index = len(cursor.batch_ids)
            items = self.snapshot.items[index * plan.batch_size : (index + 1) * plan.batch_size]
            if len(items) < plan.batch_size:
                return None
            require_unclaimed(db, items)
            execution = BatchExecution(
                batch_size=plan.batch_size,
                publications_per_batch=plan.publications_per_batch,
                plan_revision=revision,
                trigger="manual",
                publication_mode=plan.publication_mode,
                skip_review_rejections=self.skip_review_rejections,
                editing_revision="v005",
                producer_consumer=True,
            )
            service = BatchTriggerService(
                self.runtime.factory, self.snapshot.policy, self.runtime.tasks.clock
            )
            batch = service.freeze_items(
                db,
                items,
                f"backlog:{self.sha256}:{index}",
                execution,
                snapshot_at=parse_stamp(self.snapshot.captured_at),
            )
            cursor.batch_ids.append(batch.id)
            if saved:
                saved.value_json, saved.revision = cursor.model_dump_json(), saved.revision + 1
            else:
                db.add(
                    WorkflowSetting(id=self.key, revision=1, value_json=cursor.model_dump_json())
                )
            return batch


def validate_cursor(db: Session, cursor: BacklogCursor) -> None:
    for index, identifier in enumerate(cursor.batch_ids):
        batch = db.get(Batch, identifier)
        if batch is None or batch.run_key != f"backlog:{cursor.snapshot_sha256}:{index}":
            raise WorkflowError("BACKLOG_CURSOR_INVALID", "积压游标缺少匹配批次，不能跳过素材。")


def require_unclaimed(db: Session, items: list[VideoDTO]) -> None:
    ids = [item.video_id for item in items]
    rows = {row.id: row for row in db.scalars(select(Video).where(Video.id.in_(ids)))}
    if any(
        item.video_id not in rows or rows[item.video_id].channel_id != item.channel_id
        for item in items
    ):
        raise WorkflowError(
            "BACKLOG_SOURCE_CHANGED", "积压视频缺失或来源身份改变，未自动替换候选。"
        )
    reserved = db.scalar(
        select(BatchItem.video_id)
        .join(Batch)
        .where(BatchItem.video_id.in_(ids), Batch.reservations_released.is_(False))
        .limit(1)
    )
    prepared = db.scalar(
        select(Task.id)
        .where(
            Task.kind == "prepare_video", func.json_extract(Task.input_json, "$.video_id").in_(ids)
        )
        .limit(1)
    )
    published = db.scalar(
        select(Publication.id)
        .where(
            func.json_extract(Publication.manifest_json, "$.source_url").in_(
                [f"https://www.youtube.com/watch?v={identifier}" for identifier in ids]
            )
        )
        .limit(1)
    )
    if reserved or prepared or published:
        raise WorkflowError(
            "BACKLOG_ALREADY_CLAIMED", "本片段已有其他处理记录，先对账，不跳过或重复制作。"
        )
