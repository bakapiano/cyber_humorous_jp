from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Response
from pydantic import ValidationError

from youmo_workflow.adapters.node import node_request
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.youtube_login import YoutubeConnection, YoutubePairing
from youmo_workflow.runtime import Runtime


def extension_archive(root: Path) -> bytes:
    directory = root / "extensions/youtube-login/dist"
    if not (directory / "manifest.json").is_file():
        raise WorkflowError("EXTENSION_NOT_BUILT", "扩展尚未构建，请运行 pnpm build。")
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix not in {".js", ".html", ".css", ".json"}:
                continue
            if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink():
                raise WorkflowError("EXTENSION_INVALID", "扩展构建产物无效。")
            archive.write(path, path.relative_to(directory).as_posix())
    return output.getvalue()


def youtube_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/youtube", tags=["YouTube connection"])

    async def connection(method: str, path: str) -> YoutubeConnection:
        raw = await node_request(runtime.settings.node_token_path, method, "/auth/" + path)
        try:
            return YoutubeConnection.model_validate_json(raw)
        except ValidationError:
            raise WorkflowError("NODE_SCHEMA_MISMATCH", "登录服务返回不符合类型契约。") from None

    @router.get("/connection", response_model=YoutubeConnection)
    async def status() -> YoutubeConnection:
        return await connection("GET", "status")

    @router.post("/pairing", response_model=YoutubePairing)
    async def pairing() -> YoutubePairing:
        raw = await node_request(runtime.settings.node_token_path, "POST", "/auth/pairing")
        try:
            return YoutubePairing.model_validate_json(raw)
        except ValidationError:
            raise WorkflowError("NODE_SCHEMA_MISMATCH", "配对服务返回不符合类型契约。") from None

    @router.post("/verify", response_model=YoutubeConnection)
    async def verify() -> YoutubeConnection:
        return await connection("POST", "verify")

    @router.delete("/connection", response_model=YoutubeConnection)
    async def disconnect() -> YoutubeConnection:
        return await connection("DELETE", "session")

    @router.get("/extension.zip", response_class=Response)
    def download_extension() -> Response:
        return Response(
            extension_archive(runtime.settings.root),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="youmo-youtube-login.zip"'},
        )

    return router
