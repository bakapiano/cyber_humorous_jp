from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.scan_plan import ScanPlanState, ScanPlanUpdate, StoredScanPlan
from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.storage.models import Task, WorkflowSetting

SCAN_SETTING = "channel_scan_plan"
SCAN_KINDS = ("sync_source", "sync_home")


def read_scan_plan(db: Session) -> tuple[StoredScanPlan, int]:
    row = db.get(WorkflowSetting, SCAN_SETTING)
    return (
        (StoredScanPlan.model_validate_json(row.value_json), row.revision)
        if row
        else (StoredScanPlan(), 0)
    )


def scanning(db: Session) -> bool:
    return bool(
        db.scalar(
            select(Task.id)
            .where(Task.kind.in_(SCAN_KINDS), Task.status.in_(["pending", "running", "retry_wait"]))
            .limit(1)
        )
    )


def store_scan_plan(db: Session, plan: StoredScanPlan, revision: int) -> None:
    row = db.get(WorkflowSetting, SCAN_SETTING)
    if row is None:
        row = WorkflowSetting(id=SCAN_SETTING)
        db.add(row)
    row.value_json, row.revision = plan.model_dump_json(), revision


class ScanPlanService:
    def __init__(
        self, factory: sessionmaker[Session], clock: Callable[[], datetime] = now_utc
    ) -> None:
        self.factory, self.clock = factory, clock

    def state(self) -> ScanPlanState:
        with self.factory() as db:
            plan, revision = read_scan_plan(db)
            return ScanPlanState(**plan.model_dump(), revision=revision, scanning=scanning(db))

    def update(self, request: ScanPlanUpdate) -> ScanPlanState:
        with self.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            _, revision = read_scan_plan(db)
            if request.revision != revision:
                raise WorkflowError("SCAN_PLAN_CHANGED", "扫描配置已更新，请加载最新配置。")
            plan = StoredScanPlan(
                enabled=request.enabled,
                interval_minutes=request.interval_minutes,
                next_run_at=stamp(self.clock() + timedelta(minutes=request.interval_minutes))
                if request.enabled
                else None,
            )
            store_scan_plan(db, plan, revision + 1)
        return self.state()
