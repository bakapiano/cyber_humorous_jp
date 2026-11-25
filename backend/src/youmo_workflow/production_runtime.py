import os
from pathlib import Path

import httpx

from youmo_workflow.adapters.acquisition import YoutubeAcquisition
from youmo_workflow.adapters.content_preparation import ContentProviders, ProductionContent
from youmo_workflow.adapters.download_session import NodeDownloadSession
from youmo_workflow.adapters.editing import ProductionEditor
from youmo_workflow.adapters.model_http import CoverFallback, load_retry_policy
from youmo_workflow.adapters.model_route import ModelRoute
from youmo_workflow.adapters.packaging import ProductionPackaging
from youmo_workflow.adapters.production_model import ProductionModel
from youmo_workflow.adapters.production_profile import ProductionProfile
from youmo_workflow.adapters.selection_completion import read_deepseek_key
from youmo_workflow.adapters.transcription import LocalTranscription
from youmo_workflow.adapters.translation_review import TranslationReview
from youmo_workflow.config import existing_proxy
from youmo_workflow.runtime import Runtime


def build_production(
    runtime: Runtime,
    client: httpx.Client,
    *,
    model_client: httpx.Client,
    model_route: ModelRoute = ModelRoute.DIRECT,
    cover_fallback_client: httpx.Client | None = None,
) -> tuple[ProductionContent, ProductionPackaging, ProductionEditor]:
    workspace = runtime.settings.root.parent
    gateway = model_route is ModelRoute.WORKBUDDY
    fallback = (
        CoverFallback(
            cover_fallback_client,
            lambda: read_deepseek_key(ModelRoute.DIRECT.credential_path(runtime.settings.root)),
        )
        if gateway and cover_fallback_client is not None
        else None
    )
    model = ProductionModel(
        model_client,
        model_route.credential_path(runtime.settings.root),
        runtime.settings.root / "data",
        load_retry_policy(runtime.settings.root),
        route=model_route,
    )
    model.cover_fallback = fallback
    profile = ProductionProfile(workspace)
    # Default to the already-authorized extension session, in memory only.
    # A configured downloader file is an explicit override, never a silent fallback.
    configured = os.environ.get("WORKFLOW_DOWNLOAD_COOKIE_FILE")
    cookie_path = Path(configured) if configured else None
    loader = (
        None
        if configured
        else NodeDownloadSession(
            runtime.settings.node_token_path,
            client,
            journal=runtime.settings.root / "data/download-session-attempts.jsonl",
        ).load
    )
    content = ProductionContent(
        runtime.artifacts,
        ContentProviders(
            YoutubeAcquisition(
                cookie_path,
                existing_proxy(runtime.settings),
                workspace / "0_youtube_collector",
                loader,
            ),
            LocalTranscription(),
            model,
            TranslationReview(
                runtime.settings.root / "config/subtitle-denylist.json", model.replay
            ),
        ),
    )
    return (
        content,
        ProductionPackaging(runtime.artifacts, model, profile),
        ProductionEditor(
            runtime.artifacts,
            model,
            profile,
        ),
    )
