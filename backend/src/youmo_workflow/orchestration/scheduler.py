from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.time import now_utc
from youmo_workflow.features.batch_plan import read_plan
from youmo_workflow.features.batch_trigger import BatchTriggerService


class Scheduler:
    def __init__(
        self,
        factory: sessionmaker[Session],
        policy: Policy,
        clock: Callable[[], datetime] = now_utc,
        publication_ready: Callable[[], bool] | None = None,
    ) -> None:
        self.factory, self.policy, self.clock = factory, policy, clock
        self.publication_ready = publication_ready
        self.backlog_tick: Callable[[], bool] | None = None

    def tick(self) -> None:
        if self.backlog_tick and self.backlog_tick():
            return
        with self.factory() as db:
            plan, _ = read_plan(db)
        if plan.auto_enabled:
            BatchTriggerService(
                self.factory, self.policy, self.clock, self.publication_ready
            ).trigger()
