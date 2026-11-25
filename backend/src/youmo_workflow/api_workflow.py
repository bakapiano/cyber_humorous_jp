from fastapi import APIRouter
from fastapi.responses import FileResponse

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import BatchDetail, BatchDTO, Dashboard, TaskDTO
from youmo_workflow.domain.providers import ArtifactRef
from youmo_workflow.domain.publication import (
    ArtifactDTO,
    Confirmation,
    PublicationDTO,
    PublicationManifest,
    SubmitPlan,
)
from youmo_workflow.domain.selection import SelectionStagesDTO
from youmo_workflow.features.dashboard import dashboard
from youmo_workflow.features.initial_selection import enqueue_initial, selection_results
from youmo_workflow.runtime import Runtime


def workflow_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/dashboard", response_model=Dashboard)
    def overview() -> Dashboard:
        flags = (runtime.settings.automation_enabled, runtime.settings.publication_enabled)
        return dashboard(runtime.factory, runtime.policy, flags)

    @router.get("/batches", response_model=list[BatchDTO])
    def batches() -> list[BatchDTO]:
        return runtime.batches.list_batches()

    @router.get("/batches/{batch_id}", response_model=BatchDetail)
    def batch(batch_id: str) -> BatchDetail:
        return runtime.batches.detail(batch_id)

    @router.post("/batches/{batch_id}/initial-selection", response_model=TaskDTO, status_code=202)
    def start_initial(batch_id: str) -> TaskDTO:
        return enqueue_initial(runtime.factory, batch_id)

    @router.get("/batches/{batch_id}/selection-results", response_model=SelectionStagesDTO)
    def results(batch_id: str) -> SelectionStagesDTO:
        return selection_results(runtime.factory, batch_id)

    @router.get("/tasks", response_model=list[TaskDTO])
    def tasks() -> list[TaskDTO]:
        return runtime.tasks.list_tasks()

    @router.post("/tasks/{task_id}/retry", response_model=TaskDTO, status_code=202)
    def retry(task_id: str) -> TaskDTO:
        return runtime.tasks.retry(task_id)

    return router


def publication_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    @router.get("/publications", response_model=list[PublicationDTO])
    def publications() -> list[PublicationDTO]:
        return runtime.publications.list_publications()

    @router.post("/publications/prepare", response_model=PublicationDTO)
    def prepare(body: PublicationManifest) -> PublicationDTO:
        return runtime.publications.prepare(body)

    @router.post("/publications/{publication_id}/confirm", response_model=PublicationDTO)
    def confirm(publication_id: str, body: Confirmation) -> PublicationDTO:
        return runtime.publications.confirm(publication_id, body.manifest_hash)

    @router.get("/publications/{publication_id}/plan", response_model=SubmitPlan)
    def plan(publication_id: str) -> SubmitPlan:
        return runtime.publications.plan(publication_id)

    @router.get("/artifacts/{artifact_id}", response_model=ArtifactDTO)
    def artifact(artifact_id: str) -> ArtifactDTO:
        return runtime.artifacts.describe(artifact_id)

    @router.get("/artifacts/{artifact_id}/preview")
    def preview(artifact_id: str) -> FileResponse:
        row = runtime.artifacts.get(artifact_id)
        reference = ArtifactRef(artifact_id=row.id, sha256=row.sha256)
        path = runtime.artifacts.resolve(reference, row.kind)
        return FileResponse(
            path,
            filename=f"{row.id}{path.suffix}",
            content_disposition_type="inline",
            media_type=preview_type(path.suffix),
        )

    return router


def preview_type(suffix: str) -> str:
    safe_types = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".srt": "text/plain",
        ".ass": "text/plain",
        ".json": "text/plain",
        ".txt": "text/plain",
    }
    media_type = safe_types.get(suffix.lower())
    if not media_type:
        raise WorkflowError("PREVIEW_TYPE_DENIED", "该产物类型不允许内联预览。")
    return media_type
