import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from youmo_workflow.adapters.node import home_feed
from youmo_workflow.api_batch_plan import batch_plan_router
from youmo_workflow.api_remote import remote_router
from youmo_workflow.api_sources import source_router
from youmo_workflow.api_usage import usage_router
from youmo_workflow.api_workflow import publication_router, workflow_router
from youmo_workflow.api_workspace import workspace_router
from youmo_workflow.api_youtube import youtube_router
from youmo_workflow.config import Settings, get_settings
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import DiscoveryPage, ErrorResponse, Health, SessionInfo
from youmo_workflow.runtime import Runtime, build_runtime, initialize


def create_app(runtime: Runtime | None = None) -> FastAPI:
    current = runtime if runtime is not None else build_runtime(get_settings())
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        if not current.settings.testing:
            initialize(current)
        yield

    app = FastAPI(
        title="Youmo Workflow",
        version="0.1.0",
        lifespan=lifespan,
        responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    )
    install_security(app, current.settings, token)

    @app.exception_handler(WorkflowError)
    async def workflow_error(_request: Request, error: WorkflowError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"code": error.code, "message": error.message})

    @app.get("/api/v1/health", response_model=Health)
    def health() -> Health:
        return Health()

    @app.get("/api/v1/session", response_model=SessionInfo)
    def session() -> SessionInfo:
        return SessionInfo(csrf_token=token)

    @app.get("/api/v1/recommendations/preview", response_model=DiscoveryPage)
    async def recommendations() -> DiscoveryPage:
        return await home_feed(current.settings.node_token_path)

    app.include_router(source_router(current))
    app.include_router(youtube_router(current))
    app.include_router(workflow_router(current))
    app.include_router(batch_plan_router(current))
    app.include_router(publication_router(current))
    app.include_router(workspace_router(current))
    app.include_router(usage_router(current))
    app.include_router(remote_router(current))
    assets = current.settings.root / "frontend/dist"
    if assets.is_dir():
        app.mount("/", StaticFiles(directory=assets, html=True), name="frontend")
    return app


def install_security(app: FastAPI, settings: Settings, token: str) -> None:
    @app.middleware("http")
    async def secure(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        host = urlparse("http://" + request.headers.get("host", "")).hostname
        allowed = {"127.0.0.1", "localhost"} | ({"testserver"} if settings.testing else set[str]())
        if host not in allowed:
            return JSONResponse(
                status_code=403, content={"code": "LOCAL_ONLY", "message": "仅限本机访问。"}
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            denied = check_write_request(request, allowed, token)
            if denied:
                return denied
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        return response


def check_write_request(request: Request, hosts: set[str], token: str) -> JSONResponse | None:
    origin = request.headers.get("origin")
    allowed_origins = {f"http://{host}:{port}" for host in hosts for port in (5173, 8766)}
    if origin and origin not in allowed_origins:
        return JSONResponse(
            status_code=403, content={"code": "ORIGIN_DENIED", "message": "来源不受信。"}
        )
    supplied = request.headers.get("x-workflow-token", "")
    if not secrets.compare_digest(supplied, token):
        return JSONResponse(
            status_code=403, content={"code": "CSRF_DENIED", "message": "请求令牌无效，请刷新。"}
        )
    return None


app = create_app()
