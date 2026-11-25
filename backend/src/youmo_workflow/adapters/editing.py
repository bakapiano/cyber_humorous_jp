import hashlib
from pathlib import Path

from pydantic import JsonValue

from youmo_workflow.adapters.editing_render import RenderReceipt, remap_subtitles, render_edit
from youmo_workflow.adapters.editing_validation import (
    make_timeline,
    require_recap_feasible,
    validate_ranges,
)
from youmo_workflow.adapters.hard_subtitles import editing_layout_version
from youmo_workflow.adapters.packaging_validation import require
from youmo_workflow.adapters.production_files import encode, folder, freeze, save
from youmo_workflow.adapters.production_profile import ProductionProfile
from youmo_workflow.adapters.subtitle_llm_review import ReviewContext, SubtitleAuditor
from youmo_workflow.adapters.subtitle_splitting import segment_subtitles
from youmo_workflow.domain.burn_in import BurnInProof
from youmo_workflow.domain.content import GenerationProvider, GenerationRequest, Transcript
from youmo_workflow.domain.editing import EditingOutput, RangePlan, TimelinePart
from youmo_workflow.domain.editing_presentation import present_edit
from youmo_workflow.domain.generation import checked_generation
from youmo_workflow.domain.providers import ReadyContent
from youmo_workflow.domain.quality import ArtifactStore


class ProductionEditor:
    def __init__(
        self,
        assets: ArtifactStore,
        model: GenerationProvider,
        profile: ProductionProfile,
    ):
        self.assets, self.model, self.profile = assets, model, profile

    def edit(self, request: ReadyContent) -> EditingOutput:
        content, packaging = request.content, request.packaging
        media = self.assets.resolve(content.original_media_ref, "media")
        subtitle = self.assets.resolve(content.translation_ref, "subtitle")
        transcript = Transcript.model_validate_json(
            self.assets.resolve(content.transcript_ref, "transcript").read_bytes()
        )
        require(
            transcript.source_sha256 == content.original_media_ref.sha256
            and transcript.video_id == content.video_id,
            "字幕和原片身份不一致。",
        )
        directory = editing_directory(self.assets, request, media)
        freeze(directory / "input.json", request.model_dump_json())
        cache = directory / "output.json"
        if cache.exists():
            result = EditingOutput.model_validate_json(cache.read_bytes())
            self.assets.resolve(result.media_ref, "media")
            self.assets.resolve(result.subtitle_ref, "subtitle")
            self.assets.resolve(result.plan_ref, "edit_plan")
            require(
                result.subtitle_mode == "hard" and result.burn_in_ref is not None,
                "旧软字幕不能作为烧录成片复用。",
            )
            assert result.burn_in_ref is not None
            self.assets.resolve(result.burn_in_ref, "burn_in")
            return result
        payload: dict[str, JsonValue] = {
            "final_title": packaging.title,
            "editorial_angle": packaging.editorial_angle,
            "title_evidence": [row.model_dump(mode="json") for row in packaging.title_evidence],
            "cover_timestamp": packaging.cover_timestamp,
            "source_kind": transcript.source_kind,
            "duration_seconds": transcript.duration_seconds,
            "excluded_ranges": [],
            "original_title": content.original_title,
            "translation_srt": subtitle.read_text(encoding="utf-8"),
            "transcript": [row.model_dump(mode="json") for row in transcript.segments],
        }
        plan = self.plan(request, payload, directory, transcript)
        validate_ranges(plan, transcript, packaging, 60)
        timeline = make_timeline(plan)
        presentation = present_edit(plan) if request.editing_revision == "v005" else None
        if presentation:
            timeline = presentation.timeline
            freeze(directory / "presentation.json", presentation.model_dump_json())
        plan_path = directory / "plan.json"
        save(
            plan_path,
            encode(
                {
                    "plan": plan.model_dump(mode="json"),
                    "timeline": [part.model_dump(mode="json") for part in timeline],
                    "source_sha256": content.original_media_ref.sha256,
                }
            ),
        )
        mapped = directory / "final.zh-ja.srt"
        remap_subtitles(subtitle, timeline, mapped)
        mapped = SubtitleAuditor(self.model, self.profile.poster.parent).review(
            request,
            mapped,
            directory / "subtitle_audit_v002",
            context=ReviewContext(
                stage=(
                    "subtitle_llm_review_hardsub_v003"
                    if presentation
                    else "subtitle_llm_review_hardsub_v002"
                )
            ),
        )
        mapped = segment_subtitles(
            mapped, media, directory / "subtitle_layout_v001", timeline[-1].output_end
        )
        render_edit(
            media,
            timeline,
            directory,
            mapped,
            transition_seconds=presentation.transition_seconds if presentation else 0,
        )
        test_only = any(
            self.assets.describe(ref.artifact_id).test_only
            for ref in (
                content.original_media_ref,
                content.transcript_ref,
                content.translation_ref,
                packaging.cover_ref,
            )
        )
        return self.register_output(request, directory, timeline, mapped, test_only)

    def register_output(
        self,
        request: ReadyContent,
        directory: Path,
        timeline: list[TimelinePart],
        mapped: Path,
        test_only: bool,
    ) -> EditingOutput:
        receipt = RenderReceipt.model_validate_json((directory / "render.json").read_bytes())
        require(receipt.burn_layout is not None, "渲染未提供双语烧录证据。")
        assert receipt.burn_layout is not None
        layout = receipt.burn_layout
        proof = BurnInProof(
            media_sha256=receipt.sha256,
            subtitle_sha256=layout.subtitle_sha256,
            ass_ref=self.assets.register(
                directory / "burn-in.ass", "subtitle_ass", test_only=test_only
            ),
            font_sha256=layout.font_sha256,
            font_name=layout.font_name,
            width=layout.width,
            height=layout.height,
            cue_count=layout.cue_count,
            seconds=receipt.seconds,
        )
        proof_path = directory / "burn-proof.json"
        freeze(proof_path, proof.model_dump_json())
        result = EditingOutput(
            content_id=request.content.content_id,
            title=request.packaging.title,
            timeline=timeline,
            original_media_ref=request.content.original_media_ref,
            media_ref=self.assets.register(directory / "final.mp4", "media", test_only=test_only),
            subtitle_ref=self.assets.register(mapped, "subtitle", test_only=test_only),
            plan_ref=self.assets.register(
                directory / "plan.json", "edit_plan", test_only=test_only
            ),
            version=f"{request.editing_revision}_hardsub",
            subtitle_mode="hard",
            burn_in_ref=self.assets.register(proof_path, "burn_in", test_only=test_only),
        )
        save(directory / "output.json", result.model_dump_json())
        return result

    def plan(
        self,
        request: ReadyContent,
        payload: dict[str, JsonValue],
        directory: Path,
        transcript: Transcript,
    ) -> RangePlan:
        require_recap_feasible(transcript, request.packaging, 60)
        generated = GenerationRequest(
            content_id=request.content.content_id,
            stage="editing_v004_hardsub",
            prompt=self.profile.hard_edit_prompt(payload),
        )
        raw = checked_generation(
            self.model,
            generated,
            lambda value: validate_ranges(
                RangePlan.model_validate_json(value), transcript, request.packaging, 60
            ),
        )
        plan = RangePlan.model_validate_json(raw)
        save(
            directory / "generation.json",
            encode(
                {
                    "strategy": generated.stage,
                    "response_content_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                    "model_times_edited": False,
                    "model_quotes_used": False,
                }
            ),
        )
        return plan


def editing_directory(assets: ArtifactStore, request: ReadyContent, media: Path) -> Path:
    legacy = editing_layout_version(media).replace("v004", request.editing_revision)
    prior = assets.root / "production" / request.content.content_id / legacy
    # Completed render caches remain read-only. Only unfinished/new outputs use split layout.
    version = legacy if (prior / "output.json").is_file() else legacy.removesuffix("003") + "004"
    return folder(assets.root, request.content.content_id, version)
