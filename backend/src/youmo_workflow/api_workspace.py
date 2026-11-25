from typing import Annotated

from fastapi import APIRouter, Query

from youmo_workflow.domain.workspace_views import (
    BatchPage,
    PublicationActivity,
    PublicationPage,
    TaskPage,
)
from youmo_workflow.features.publication_activity import publication_activity
from youmo_workflow.features.workspace_pages import batch_page, publication_page, task_page
from youmo_workflow.runtime import Runtime

PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0)]


def workspace_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspace")

    @router.get("/batches", response_model=BatchPage)
    def batches(limit: PageLimit = 10, offset: PageOffset = 0) -> BatchPage:
        return batch_page(runtime.factory, limit, offset)

    @router.get("/tasks", response_model=TaskPage)
    def tasks(limit: PageLimit = 10, offset: PageOffset = 0) -> TaskPage:
        return task_page(runtime.factory, limit, offset)

    @router.get("/publications", response_model=PublicationPage)
    def publications(limit: PageLimit = 10, offset: PageOffset = 0) -> PublicationPage:
        return publication_page(runtime.factory, limit, offset)

    @router.get("/publication-activity", response_model=PublicationActivity)
    def activity(hours: Annotated[int, Query(ge=1, le=720)] = 72) -> PublicationActivity:
        return publication_activity(runtime.factory, hours, runtime.tasks.clock())

    return router
