import hashlib
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from youmo_workflow.adapters.production_files import folder, freeze, save
from youmo_workflow.adapters.translation import translate
from youmo_workflow.adapters.translation_review import TranslationReview
from youmo_workflow.domain.content import (
    AcquisitionProvider,
    GenerationProvider,
    TranscriptionProvider,
)
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import VideoDTO
from youmo_workflow.domain.providers import SelectedContent, SelectionOutput
from youmo_workflow.domain.quality import ArtifactStore
from youmo_workflow.domain.selection import InitialOutput
from youmo_workflow.domain.source_skip import DOWNLOAD_SKIP_ERRORS, SourceDownloadFailed
from youmo_workflow.domain.time import now_utc, stamp


@dataclass
class ContentProviders:
    acquisition: AcquisitionProvider
    transcription: TranscriptionProvider
    model: GenerationProvider
    translation_review: TranslationReview | None = None


class ProductionContent:
    def __init__(self, assets: ArtifactStore, providers: ContentProviders) -> None:
        self.assets, self.providers = assets, providers

    def prepare(self, request: InitialOutput) -> SelectionOutput:
        videos = {row.video.video_id: row.video for row in request.ranked}
        return SelectionOutput(
            batch_id=request.batch_id,
            rejected_ids=[],
            selected=[self.prepare_video(videos[video_id]) for video_id in request.shortlisted_ids],
        )

    def prepare_video(self, video: VideoDTO) -> SelectedContent:
        identity = hashlib.sha256(video.model_dump_json().encode()).hexdigest()[:16]
        content_id = f"yt-{video.video_id}-{identity}"
        directory = folder(self.assets.root, content_id, "content_v001")
        freeze(directory / "input.json", video.model_dump_json())
        cached = directory / "output.json"
        if cached.exists():
            result = SelectedContent.model_validate_json(cached.read_bytes())
            for ref, kind in [
                (result.original_media_ref, "media"),
                (result.transcript_ref, "transcript"),
                (result.translation_ref, "subtitle"),
            ]:
                self.assets.resolve(ref, kind)
            return result
        media = self.acquire(video, directory)
        transcript = self.providers.transcription.transcribe(video, media, directory)
        subtitle = translate(
            self.providers.model,
            content_id,
            transcript,
            directory,
            self.providers.translation_review,
        )
        result = SelectedContent(
            content_id=content_id,
            video_id=video.video_id,
            original_title=video.title,
            original_media_ref=self.assets.register(media, "media"),
            transcript_ref=self.assets.register(directory / "transcript.json", "transcript"),
            translation_ref=self.assets.register(subtitle, "subtitle"),
            evidence=["完整YouTube原片；本地机器转写；机器翻译，非人工事实审核。"],
            expires_at=stamp(now_utc() + timedelta(hours=72)),
        )
        save(cached, result.model_dump_json())
        return result

    def acquire(self, video: VideoDTO, directory: Path) -> Path:
        try:
            return self.providers.acquisition.acquire(video, directory)
        except WorkflowError as error:
            if error.code not in DOWNLOAD_SKIP_ERRORS:
                raise
            failure = SourceDownloadFailed(video.video_id, error)
            save(directory / "source-download-failure.json", failure.evidence.model_dump_json())
            raise failure from None
