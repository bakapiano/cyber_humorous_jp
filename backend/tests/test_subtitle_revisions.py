import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import Assets, Clock
from test_publications import manifest
from test_remote import FakeGateway

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.remote import RemoteSubmission
from youmo_workflow.domain.subtitle_revision import SubtitleRevisionDraft
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.subtitle_revisions import active_revision, authorize_revision
from youmo_workflow.features.tasks import add_task
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.orchestration.subtitle_revision import SubtitleRevisionService
from youmo_workflow.runtime import Runtime


class RevisionGateway(FakeGateway):
    def __init__(self, previous: Path, revised: Path):
        super().__init__()
        self.previous, self.revised = previous, revised
        self.rejected = True

    def rejected_subtitle(
        self, submission: RemoteSubmission, previous: Path
    ) -> tuple[str, str] | None:
        assert previous == self.previous
        return ("123", "synthetic rejection") if self.rejected else None

    def revise_subtitles(
        self, submission: RemoteSubmission, previous: Path, revised: Path, subtitle_id: str
    ) -> bool:
        assert previous == self.previous and revised == self.revised and subtitle_id == "123"
        self.writes += 1
        self.has_subtitle = True
        return True

    def verify_subtitles(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        return self.has_subtitle and subtitle == self.revised


def preparation(
    runtime: Runtime, assets: Assets, clock: Clock
) -> tuple[SubtitleRevisionDraft, RevisionGateway]:
    old, new = runtime.artifacts.root / "before.srt", runtime.artifacts.root / "revised.srt"
    old.write_text(
        "1\n00:00:00,000 --> 00:00:01,900\n普通文字甲乙后续信息\n日文の説明があります\n",
        encoding="utf-8",
    )
    new.write_text(old.read_text(encoding="utf-8").replace("甲乙", "**"), encoding="utf-8")
    value = manifest(assets, clock)
    value.subtitle_ref = runtime.artifacts.register(old, "subtitle", test_only=True)
    publication = runtime.publications.prepare(value)
    runtime.publications.confirm(publication.id, publication.manifest_hash)
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, publication.id)
        row.status, row.remote_id = "subtitle_rejected", "BV1234567890"
        task = add_task(
            db, "finalize_private", f"finalize:{row.id}:{row.manifest_hash}", stamp(clock())
        )
        task.input_json = FinalizePrivate(
            publication_id=row.id, manifest_hash=row.manifest_hash
        ).model_dump_json()
        task.status, task.attempts, task.max_attempts = "failed", 3, 60
        task.error_code = "SUBTITLE_REVIEW_REJECTED"
    decision_path = new.parent / "decision.json"
    decision_path.write_text(
        json.dumps({"safe_to_submit": True, "mask_spans": [], "notes": []}), encoding="utf-8"
    )
    (new.parent / "audit.json").write_text(
        json.dumps(
            {"rules_sha256": "a" * 64, "timeline_changed": False, "original_overwritten": False}
        ),
        encoding="utf-8",
    )
    draft = SubtitleRevisionDraft(
        publication_id=publication.id,
        revised_ref=runtime.artifacts.register(new, "subtitle", test_only=True),
        rules_sha256="a" * 64,
        review_sha256=hashlib.sha256(decision_path.read_bytes()).hexdigest(),
        authority="Explicit synthetic user approval for a changed subtitle",
    )
    return draft, RevisionGateway(old, new)


def test_revision_keeps_manifest_and_uses_new_subtitle_for_write_and_read(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    draft, gateway = preparation(runtime, assets, clock)
    with runtime.factory() as db:
        before = runtime.publications.require(db, draft.publication_id).manifest_json
    record = SubtitleRevisionService(runtime, gateway).authorize(draft)
    assert gateway.writes == 0
    assert authorize_revision(runtime.factory, record) == record.revision_id
    with runtime.factory() as db:
        row = runtime.publications.require(db, draft.publication_id)
        assert row.manifest_json == before and row.status == "awaiting_subtitles"
        assert active_revision(db, row) == record
    live = replace(runtime, settings=replace(runtime.settings, publication_enabled=True))
    executor = PublicationExecutor(live, gateway, legacy_native_subtitles=True)
    assert executor.attach_subtitles(draft.publication_id) == "completed"
    assert executor.reconcile(draft.publication_id) == "completed" and gateway.writes == 1
    assert "甲乙" in gateway.previous.read_text(encoding="utf-8")


@pytest.mark.parametrize("status", ["subtitle_unknown", "subtitle_submitting", "completed"])
def test_uncertain_or_completed_captions_cannot_be_reset(
    runtime: Runtime, assets: Assets, clock: Clock, status: str
) -> None:
    draft, gateway = preparation(runtime, assets, clock)
    with runtime.factory.begin() as db:
        runtime.publications.require(db, draft.publication_id).status = status
    with pytest.raises(WorkflowError, match="已明确退回"):
        SubtitleRevisionService(runtime, gateway).authorize(draft)
    assert gateway.writes == 0


def test_factual_rewrite_is_rejected(runtime: Runtime, assets: Assets, clock: Clock) -> None:
    draft, gateway = preparation(runtime, assets, clock)
    gateway.revised.write_text(
        gateway.revised.read_text(encoding="utf-8").replace("**", "丙丁"), encoding="utf-8"
    )
    draft.revised_ref = runtime.artifacts.register(gateway.revised, "subtitle", test_only=True)
    with pytest.raises(WorkflowError, match="只能逐字增加遮罩"):
        SubtitleRevisionService(runtime, gateway).authorize(draft)


def test_missing_remote_rejection_does_not_authorize_a_write(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    draft, gateway = preparation(runtime, assets, clock)
    gateway.rejected = False
    with pytest.raises(WorkflowError, match="远端没有匹配"):
        SubtitleRevisionService(runtime, gateway).authorize(draft)


def test_revision_budget_does_not_reset_on_retry(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    draft, gateway = preparation(runtime, assets, clock)
    record = SubtitleRevisionService(runtime, gateway).authorize(draft)
    with pytest.raises(WorkflowError, match="额度已使用"):
        authorize_revision(runtime.factory, record.model_copy(update={"revision_id": "c" * 32}))
    task = runtime.tasks.claim(("finalize_private",))
    assert task and task.attempts == 4


def test_model_content_refusal_cannot_be_authorized(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    draft, gateway = preparation(runtime, assets, clock)
    decision = gateway.revised.parent / "decision.json"
    decision.write_text(
        json.dumps({"safe_to_submit": False, "mask_spans": [], "notes": ["not approved"]}),
        encoding="utf-8",
    )
    draft.review_sha256 = hashlib.sha256(decision.read_bytes()).hexdigest()
    with pytest.raises(WorkflowError, match="已通过LLM"):
        SubtitleRevisionService(runtime, gateway).authorize(draft)
    assert gateway.writes == 0
