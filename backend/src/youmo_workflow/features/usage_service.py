import time
from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.usage import UsageEvent, UsageReport
from youmo_workflow.domain.usage_pricing import UsagePricing
from youmo_workflow.features.model_usage import build_usage_report
from youmo_workflow.features.usage_attribution import usage_context


class UsageReader(Protocol):
    def snapshot(self) -> tuple[list[UsageEvent], int]: ...


class UsageService:
    def __init__(
        self,
        factory: sessionmaker[Session],
        reader: UsageReader,
        pricing: UsagePricing,
        clock: Callable[[], datetime],
    ):
        self.factory, self.reader, self.pricing, self.clock = factory, reader, pricing, clock
        self.lock = RLock()
        self.cached: UsageReport | None = None
        self.loaded_at = 0.0

    def report(self, refresh: bool = False) -> UsageReport:
        with self.lock:
            if not refresh and self.cached is not None and time.monotonic() - self.loaded_at < 30:
                return self.cached
            events, duplicates = self.reader.snapshot()
            self.cached = build_usage_report(
                events, usage_context(self.factory), self.pricing, self.clock(), duplicates
            )
            self.loaded_at = time.monotonic()
            return self.cached
