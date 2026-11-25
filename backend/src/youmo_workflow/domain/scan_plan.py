from pydantic import Field

from youmo_workflow.domain.models import Contract


class ScanPlan(Contract):
    enabled: bool = True
    interval_minutes: int = Field(default=30, ge=5, le=1440, strict=True)


class ScanPlanUpdate(ScanPlan):
    revision: int = Field(ge=0)


class StoredScanPlan(ScanPlan):
    next_run_at: str | None = None


class ScanPlanState(StoredScanPlan):
    revision: int
    scanning: bool
