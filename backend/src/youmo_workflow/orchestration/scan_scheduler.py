from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.time import now_utc, parse_stamp, stamp
from youmo_workflow.features.discovery import enqueue_home, queue_discovery
from youmo_workflow.features.scan_plan import read_scan_plan, scanning, store_scan_plan


class ScanScheduler:
    def __init__(
        self,
        factory: sessionmaker[Session],
        policy: Policy,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.factory, self.policy, self.clock = factory, policy, clock
        self.home_enabled = False

    def tick(self) -> None:
        at = self.clock()
        with self.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            plan, revision = read_scan_plan(db)
            if (
                not plan.enabled
                or (plan.next_run_at and parse_stamp(plan.next_run_at) > at)
                or scanning(db)
            ):
                return
            policy = self.policy.model_copy(update={"channel_poll_minutes": plan.interval_minutes})
            try:
                queue_discovery(db, None, policy, at)
            except WorkflowError as error:
                if error.code != "NO_ENABLED_SOURCES":
                    raise
            plan.next_run_at = stamp(at + timedelta(minutes=plan.interval_minutes))
            store_scan_plan(db, plan, revision)
        if self.home_enabled:
            enqueue_home(self.factory, self.clock)
