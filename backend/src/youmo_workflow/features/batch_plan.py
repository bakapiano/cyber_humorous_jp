from collections.abc import Callable
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.batch_plan import (
    BatchExecution,
    BatchPlan,
    BatchPlanState,
    BatchPlanUpdate,
)
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.time import BUSINESS_ZONE, now_utc, parse_stamp
from youmo_workflow.features.batches import eligible_query
from youmo_workflow.features.pipeline_gates import active_holds, blocked_kinds
from youmo_workflow.features.pipeline_storage import disk_ready
from youmo_workflow.features.production_buffer import (
    BUFFER_LIMIT,
    active_producer,
    buffer_used,
)
from youmo_workflow.features.publication_cooldown import active_until, current_state
from youmo_workflow.features.remote_hold import remote_held
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting

SETTING_ID = "quantity_batch_plan"
ACTIVE_TASKS = ("pending", "running", "retry_wait")


def batch_editing_revision(db: Session, batch_id: str | None) -> Literal["v004", "v005"]:
    batch = db.get(Batch, batch_id) if batch_id else None
    if batch is None or batch.execution_json is None:
        return "v004"
    return BatchExecution.model_validate_json(batch.execution_json).editing_revision


def read_plan(db: Session) -> tuple[BatchPlan, int]:
    row = db.get(WorkflowSetting, SETTING_ID)
    if row is None:
        return BatchPlan(), 0
    return BatchPlan.model_validate_json(row.value_json), row.revision


def active_batch(db: Session) -> str | None:
    pending = select(Task.batch_id).where(Task.batch_id.is_not(None), Task.status.in_(ACTIVE_TASKS))
    return db.scalar(
        select(Batch.id)
        .where(
            Batch.id.in_(pending)
            | Batch.status.in_(["queued", "running", "publishing", "cleaning"])
        )
        .order_by(Batch.snapshot_at, Batch.id)
        .limit(1)
    )


def automatic_blocked(db: Session) -> bool:
    return "initial_selection" in blocked_kinds(db) or not disk_ready()


class BatchPlanService:
    def __init__(
        self,
        factory: sessionmaker[Session],
        policy: Policy,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.factory, self.policy, self.clock = factory, policy, clock

    def state(self) -> BatchPlanState:
        with self.factory() as db:
            plan, revision = read_plan(db)
            count = (
                db.scalar(
                    select(func.count()).select_from(
                        eligible_query(self.clock(), self.policy).subquery()
                    )
                )
                or 0
            )
            active, blocked = active_producer(db), automatic_blocked(db)
            used = buffer_used(db)
            cooldown = active_until(db, self.clock())
            rejected = current_state(db, self.clock()).consecutive_rejections > 0
            held = remote_held(db)
            holds = active_holds(db)
        message = plan_message(plan, count, active, blocked)
        if used + plan.publications_per_batch > BUFFER_LIMIT:
            message = f"本地制作/待投名额{used}/{BUFFER_LIMIT}，等待投递清理后继续制作。"
        if cooldown:
            local = parse_stamp(cooldown).astimezone(BUSINESS_ZONE).strftime("%m-%d %H:%M")
            wait = (
                f"B站上传限流，冷却至 {local} 后自动再试；"
                if rejected
                else f"正常投稿间隔，下一次最早 {local}；"
            )
            message = wait + "扫描与制作不受影响。" + message
        if held:
            message = "B站访问待恢复：投稿和远端回查已暂缓，本地制作继续。" + message
        if holds:
            message = (
                "依赖暂停："
                + "、".join(hold.scope for hold in holds)
                + "；其他队列继续。"
                + message
            )
        return BatchPlanState(
            **plan.model_dump(),
            revision=revision,
            eligible_count=count,
            active_batch_id=active,
            automatic_blocked=blocked,
            message=message,
            buffer_used=used,
            buffer_limit=BUFFER_LIMIT,
            next_submit_at=cooldown,
        )

    def update(self, request: BatchPlanUpdate) -> BatchPlanState:
        if request.auto_enabled and not request.confirm_execution:
            raise WorkflowError(
                "EXECUTION_CONFIRMATION_REQUIRED",
                "开启自动执行须确认模型费用与所选可见性的转载投稿。",
            )
        plan = BatchPlan(
            batch_size=request.batch_size,
            publications_per_batch=request.publications_per_batch,
            auto_enabled=request.auto_enabled,
            publication_mode=request.publication_mode,
        )
        with self.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            _, revision = read_plan(db)
            if request.revision != revision:
                raise WorkflowError("PLAN_CHANGED", "配置已被其他页面修改，请重新加载后编辑。")
            row = db.get(WorkflowSetting, SETTING_ID)
            if row is None:
                row = WorkflowSetting(id=SETTING_ID)
                db.add(row)
            row.value_json, row.revision = plan.model_dump_json(), revision + 1
        return self.state()


def plan_message(plan: BatchPlan, count: int, active: str | None, blocked: bool) -> str:
    if blocked:
        return "选题的模型或存储依赖暂停；已完成稿件继续投递清理。"
    if active:
        return "分阶段队列正在并行处理；有素材和名额即可继续入队，单条失败不阻塞其他视频。"
    if count < plan.batch_size:
        return f"还差 {plan.batch_size - count} 条有效未处理素材。"
    return "素材已够一批，等待自动检测。" if plan.auto_enabled else "素材已够一批，可手动触发。"
