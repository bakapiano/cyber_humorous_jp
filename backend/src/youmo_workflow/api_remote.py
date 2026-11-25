from fastapi import APIRouter

from youmo_workflow.adapters.biliup import BiliupGateway
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime


def remote_router(runtime: Runtime) -> APIRouter:
    root = runtime.settings.root.parent / "3_bilibili_upload"
    gateway = BiliupGateway(
        root / "bin/biliupR-v1.3.4-x86_64-windows/biliup.exe",
        runtime.settings.bilibili_credentials,
        runtime.settings.artifact_root / "publication_receipts",
    )
    executor = PublicationExecutor(runtime, gateway)
    router = APIRouter(prefix="/api/v1/publications")

    @router.post("/{publication_id}/submit-private")
    def submit_private(publication_id: str) -> dict[str, str]:
        return {"remote_id": executor.submit_private(publication_id)}

    @router.post("/{publication_id}/attach-subtitles")
    def attach(publication_id: str) -> dict[str, str]:
        return {"status": executor.attach_subtitles(publication_id)}

    @router.get("/{publication_id}/verify")
    def verify(publication_id: str) -> dict[str, str]:
        return {"status": executor.reconcile(publication_id)}

    return router
