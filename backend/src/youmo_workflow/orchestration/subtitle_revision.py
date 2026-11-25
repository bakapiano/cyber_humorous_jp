from pathlib import Path
from uuid import uuid4

from pydantic import JsonValue, TypeAdapter

from youmo_workflow.adapters.bilibili_subtitles import local_cues
from youmo_workflow.adapters.production_files import digest
from youmo_workflow.adapters.subtitle_llm_review import ReviewDecision
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.remote import PublicationGateway, SubtitleRevisionGateway
from youmo_workflow.domain.subtitle_revision import SubtitleRevision, SubtitleRevisionDraft
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.subtitle_revisions import authorize_revision
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime


class SubtitleRevisionService:
    def __init__(self, runtime: Runtime, gateway: PublicationGateway):
        self.runtime, self.gateway = runtime, gateway

    def authorize(self, draft: SubtitleRevisionDraft) -> SubtitleRevision:
        if not isinstance(self.gateway, SubtitleRevisionGateway):
            raise WorkflowError("SUBTITLE_REVISION_UNSUPPORTED", "当前字幕适配器不支持修订。")
        executor = PublicationExecutor(self.runtime, self.gateway)
        manifest, remote, status = executor.remote_context(draft.publication_id)
        if status != "subtitle_rejected" or manifest.subtitle_ref is None:
            raise WorkflowError("SUBTITLE_REVISION_NOT_ELIGIBLE", "仅可修订已明确退回的字幕。")
        previous = self.runtime.artifacts.resolve(manifest.subtitle_ref, "subtitle")
        revised = self.runtime.artifacts.resolve(draft.revised_ref, "subtitle")
        self.check_audit(revised, draft)
        before, after = local_cues(previous), local_cues(revised)
        if len(before) != len(after) or not any(
            a.content != b.content for a, b in zip(before, after, strict=True)
        ):
            raise WorkflowError(
                "SUBTITLE_REVISION_UNCHANGED", "字幕没有内容变化或段数改变，未排队。"
            )
        for old, new in zip(before, after, strict=True):
            if (
                old.start != new.start
                or old.end != new.end
                or old.location != new.location
                or len(old.content) != len(new.content)
                or any(
                    a != b and (b != "*" or a.isspace())
                    for a, b in zip(old.content, new.content, strict=True)
                )
            ):
                raise WorkflowError(
                    "SUBTITLE_REVISION_NOT_MASK_ONLY",
                    "字幕修订只能逐字增加遮罩，不能改时间轴或正文。",
                )
        if self.runtime.subtitle_check:
            self.runtime.subtitle_check(revised)
        rejected = self.gateway.rejected_subtitle(remote, previous)
        if rejected is None:
            raise WorkflowError(
                "SUBTITLE_REVISION_NOT_REJECTED", "远端没有匹配旧字幕的退回记录，未排队。"
            )
        with self.runtime.factory() as db:
            row = self.runtime.publications.require(db, draft.publication_id)
            manifest_hash = row.manifest_hash
        revision = SubtitleRevision(
            **draft.model_dump(),
            revision_id=uuid4().hex,
            manifest_hash=manifest_hash,
            remote_id=remote.archive.bvid,
            base_ref=manifest.subtitle_ref,
            previous_ref=manifest.subtitle_ref,
            rejected_subtitle_id=rejected[0],
            rejection_reason=rejected[1],
            authorized_at=stamp(self.runtime.tasks.clock()),
        )
        authorize_revision(self.runtime.factory, revision)
        return revision

    def check_audit(self, revised: Path, draft: SubtitleRevisionDraft) -> None:
        decision_path = self.runtime.artifacts.safe_path(revised.parent / "decision.json")
        audit_path = self.runtime.artifacts.safe_path(revised.parent / "audit.json")
        decision = ReviewDecision.model_validate_json(decision_path.read_bytes())
        audit = TypeAdapter(dict[str, JsonValue]).validate_json(audit_path.read_bytes())
        if (
            digest(decision_path) != draft.review_sha256
            or not decision.safe_to_submit
            or audit.get("rules_sha256") != draft.rules_sha256
            or audit.get("timeline_changed") is not False
            or audit.get("original_overwritten") is not False
        ):
            raise WorkflowError(
                "SUBTITLE_REVISION_AUDIT_FAILED", "字幕修订缺少匹配的已通过LLM/程序复审记录。"
            )
