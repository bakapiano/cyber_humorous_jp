from pathlib import Path

from pydantic import JsonValue

from youmo_workflow.adapters.packaging_frames import extract_frames, render_covers
from youmo_workflow.adapters.packaging_models import CoverChoice, CoverPlanning, Frame
from youmo_workflow.adapters.packaging_parse import parse_planning
from youmo_workflow.adapters.packaging_validation import (
    check_choice,
    require,
)
from youmo_workflow.adapters.production_files import folder, freeze, save
from youmo_workflow.adapters.production_profile import ProductionProfile
from youmo_workflow.adapters.raw_titles import (
    RawTitles,
    check_title_binding,
    parse_titles,
    title_input,
)
from youmo_workflow.domain.content import (
    GenerationProvider,
    GenerationRequest,
    ModelImage,
    Transcript,
)
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.generation import checked_generation
from youmo_workflow.domain.providers import PackagingOutput, PackagingRequest
from youmo_workflow.domain.quality import ArtifactStore


class ProductionPackaging:
    def __init__(
        self, assets: ArtifactStore, model: GenerationProvider, profile: ProductionProfile
    ):
        self.assets, self.model, self.profile = assets, model, profile

    def package(self, request: PackagingRequest) -> PackagingOutput:
        content = request.content
        media = self.assets.resolve(content.original_media_ref, "media")
        transcript = Transcript.model_validate_json(
            self.assets.resolve(content.transcript_ref, "transcript").read_bytes()
        )
        require(
            transcript.source_sha256 == content.original_media_ref.sha256
            and transcript.video_id == content.video_id,
            "字幕与包装原片身份不一致。",
        )
        directory = packaging_directory(self.assets.root, content.content_id)
        freeze(directory / "input.json", request.model_dump_json())
        cached = directory / "output.json"
        if cached.exists():
            output = PackagingOutput.model_validate_json(cached.read_bytes())
            self.assets.resolve(output.cover_ref, "cover")
            return output
        settings = self.profile.renderer(poster=True)
        freeze(directory / "render_settings.json", settings.model_dump_json())
        titles = self.generate_titles(request, transcript, directory)
        plan = self.plan_cover(request, transcript, titles, directory)
        frames = extract_frames(media, content.content_id, plan, directory / "frames")
        choice = self.choose_frame(content.content_id, plan, transcript, frames, directory)
        frame = next(row for row in frames if row.frame_id == choice.selected_frame_id)
        render_covers(choice, frame, directory, settings)
        title = next(row for row in plan.titles if row.title_id == titles.recommended_title_id)
        test_only = self.assets.describe(content.original_media_ref.artifact_id).test_only
        output = PackagingOutput(
            content_id=content.content_id,
            title=title.text,
            title_evidence=title.evidence,
            editorial_angle=plan.core_angle,
            cover_timestamp=frame.timestamp_sec,
            original_media_ref=content.original_media_ref,
            packaging_version="raw_history_poster_v001",
            cover_ref=self.assets.register(directory / "cover_A.png", "cover", test_only=test_only),
            warnings=plan.warnings + choice.warnings,
        )
        save(cached, output.model_dump_json())
        return output

    def generate_titles(
        self, request: PackagingRequest, transcript: Transcript, directory: Path
    ) -> RawTitles:
        titles = parse_titles(
            checked_generation(
                self.model,
                GenerationRequest(
                    content_id=request.content.content_id,
                    stage="title_raw_history_v001",
                    prompt=self.profile.title_prompt(title_input(request, transcript)),
                ),
                parse_titles,
            )
        )
        freeze(directory / "titles.json", titles.model_dump_json())
        return titles

    def plan_cover(
        self, request: PackagingRequest, transcript: Transcript, titles: RawTitles, directory: Path
    ) -> CoverPlanning:
        payload = packaging_input(request, transcript)
        payload["publication_titles"] = titles.model_dump(mode="json")
        raw = checked_generation(
            self.model,
            GenerationRequest(
                content_id=request.content.content_id,
                stage="cover_plan_raw_history_v001",
                prompt=self.profile.cover_prompt(payload),
            ),
            lambda value: check_title_binding(parse_planning(value, directory), titles, transcript),
        )
        plan = parse_planning(raw, directory)
        check_title_binding(plan, titles, transcript)
        return plan

    def choose_frame(
        self,
        content_id: str,
        plan: CoverPlanning,
        transcript: Transcript,
        frames: list[Frame],
        directory: Path,
    ) -> CoverChoice:
        involved = {sid for region in plan.cover_regions for sid in region.evidence_segment_ids}
        payload: dict[str, JsonValue] = {
            "round1_result": plan.model_dump(mode="json"),
            "relevant_transcript": [
                row.model_dump(mode="json")
                for row in transcript.segments
                if row.segment_id in involved
            ],
            "frames": [row.model_dump(mode="json") for row in frames],
        }
        choice = CoverChoice.model_validate_json(
            checked_generation(
                self.model,
                GenerationRequest(
                    content_id=content_id,
                    stage="cover_vision_raw_history_v001",
                    prompt=self.profile.vision_prompt(payload),
                    images=[
                        ModelImage(
                            label=f"{row.frame_id} | {row.timestamp_sec:.3f}s | {row.region_id}",
                            path=directory / "frames" / f"{row.frame_id}.jpg",
                            sha256=row.sha256,
                        )
                        for row in frames
                    ],
                ),
                lambda raw: check_choice(
                    CoverChoice.model_validate_json(raw), plan, frames, poster=True
                ),
            )
        )
        check_choice(choice, plan, frames, poster=True)
        save(directory / "cover_choice.json", choice.model_dump_json())
        return choice


def packaging_directory(root: Path, content_id: str) -> Path:
    current = folder(root, content_id, "packaging_raw_history_v001")
    legacy = [
        current.parent / name
        for name in ("packaging_v004", "packaging_poster_v001")
        if any((current.parent / name / file).exists() for file in ("input.json", "output.json"))
    ]
    if not legacy:
        return current
    require(
        len(legacy) == 1 and not (current / "input.json").exists(), "包装版本冲突，需显式迁移。"
    )
    directory = legacy[0].resolve()
    require(directory.is_relative_to(root.resolve()), "历史包装目录越界。")
    if not all((directory / file).is_file() for file in ("input.json", "output.json")):
        raise WorkflowError(
            "PACKAGING_VERSION_RETIRED",
            "旧标题生成版本已移除；未完成包装需显式迁移，未追加模型调用。",
        )
    return directory


def packaging_input(request: PackagingRequest, transcript: Transcript) -> dict[str, JsonValue]:
    return {
        "original_source_titles": [{"title": request.content.original_title}],
        "transcript": [row.model_dump(mode="json") for row in transcript.segments],
        "duration_seconds": transcript.duration_seconds,
        "timebase": "youtube_original; local_video_seconds",
    }
