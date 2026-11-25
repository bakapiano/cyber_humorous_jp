from typing import Annotated

from fastapi import APIRouter, Query

from youmo_workflow.domain.models import SourceDTO, SourceUpdate, TaskDTO, VideoPage
from youmo_workflow.domain.scan_plan import ScanPlanState, ScanPlanUpdate
from youmo_workflow.features.discovery import enqueue_discovery, enqueue_home
from youmo_workflow.features.scan_plan import ScanPlanService
from youmo_workflow.runtime import Runtime


def source_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    scan = ScanPlanService(runtime.factory, runtime.tasks.clock)

    @router.get("/scan-plan", response_model=ScanPlanState)
    def scan_plan() -> ScanPlanState:
        return scan.state()

    @router.put("/scan-plan", response_model=ScanPlanState)
    def update_scan_plan(body: ScanPlanUpdate) -> ScanPlanState:
        return scan.update(body)

    @router.get("/sources", response_model=list[SourceDTO])
    def sources() -> list[SourceDTO]:
        return runtime.sources.list_sources()

    @router.patch("/sources/{source_id}", response_model=list[SourceDTO])
    def update(source_id: str, body: SourceUpdate) -> list[SourceDTO]:
        runtime.sources.set_enabled(source_id, body.enabled)
        return runtime.sources.list_sources()

    @router.get("/videos", response_model=VideoPage)
    def videos(
        source_id: str = "",
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 24,
    ) -> VideoPage:
        return runtime.sources.list_videos(source_id, offset, limit)

    @router.post("/discovery-runs", response_model=list[TaskDTO], status_code=202)
    def discover(source_id: str | None = None) -> list[TaskDTO]:
        policy = runtime.policy.model_copy(
            update={"channel_poll_minutes": scan.state().interval_minutes}
        )
        result = enqueue_discovery(runtime.factory, source_id, policy=policy)
        if source_id is None and runtime.settings.recommendations_enabled:
            result.append(enqueue_home(runtime.factory))
        return result

    return router
