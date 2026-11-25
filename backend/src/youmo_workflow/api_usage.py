from fastapi import APIRouter

from youmo_workflow.adapters.usage_journal import UsageJournal
from youmo_workflow.domain.usage import UsageReport
from youmo_workflow.domain.usage_pricing import UsagePricing
from youmo_workflow.features.usage_service import UsageService
from youmo_workflow.runtime import Runtime


def usage_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    prices = UsagePricing.model_validate_json(
        (runtime.settings.root / "config/deepseek-pricing.json").read_bytes()
    )
    service = UsageService(
        runtime.factory, UsageJournal(runtime.settings.root / "data"), prices, runtime.tasks.clock
    )

    @router.get("/model-usage", response_model=UsageReport)
    def usage(refresh: bool = False) -> UsageReport:
        return service.report(refresh)

    return router
