from collections.abc import Callable
from pathlib import Path

from youmo_workflow.adapters.hard_subtitles import verify_burn_proof
from youmo_workflow.adapters.media import check_cover, check_subtitles, probe
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.quality import ArtifactReader


class QualityGate:
    def __init__(
        self, artifacts: ArtifactReader, subtitle_check: Callable[[Path], None] | None = None
    ) -> None:
        self.artifacts = artifacts
        self.subtitle_check = subtitle_check

    def verify(self, manifest: PublicationManifest) -> None:
        if manifest.subtitle_mode == "hard":
            self.verify_burn(manifest)
        media = self.artifacts.resolve(manifest.media_ref, "media")
        cover = self.artifacts.resolve(manifest.cover_ref, "cover")
        media_info = probe(media)
        check_cover(cover)
        refs = [manifest.media_ref, manifest.cover_ref]
        if manifest.subtitle_mode == "soft" and manifest.subtitle_ref is None:
            raise WorkflowError("SUBTITLES_REQUIRED", "软字幕发布包必须包含字幕文件。")
        if manifest.subtitle_ref:
            subtitle = self.artifacts.resolve(manifest.subtitle_ref, "subtitle")
            check_subtitles(subtitle, media_info.duration_seconds)
            if self.subtitle_check:
                self.subtitle_check(subtitle)
            refs.append(manifest.subtitle_ref)
        if not manifest.test_only and any(
            self.artifacts.describe(r.artifact_id).test_only for r in refs
        ):
            raise WorkflowError("TEST_ARTIFACT_DENIED", "测试产物不能伪装成真实发布包。")

    def verify_burn(self, manifest: PublicationManifest) -> float:
        return verify_burn_proof(self.artifacts, manifest)
