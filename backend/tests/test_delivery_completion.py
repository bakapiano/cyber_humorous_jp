import json
from dataclasses import replace

import pytest
from batch_pipeline_support import offline_live_assets
from conftest import Assets, Clock
from test_hard_subtitles import burned
from test_remote import FakeGateway

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.burn_in import BurnInProof
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.remote import RemoteSubmission
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_progress import refresh_batch_progress
from youmo_workflow.orchestration.cleanup import BatchMediaCleanup
from youmo_workflow.orchestration.finalization import PrivateFinalizer
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task


class ReceiptOnlyGateway(FakeGateway):
    def inspect(self, bvid: str) -> RemoteSubmission:
        raise AssertionError("Successful delivery must not query review or public visibility")


def delivered(runtime: Runtime, clock: Clock, assets: Assets) -> tuple[Task, ReceiptOnlyGateway]:
    value = burned(runtime, offline_live_assets(runtime, assets), clock)
    value.media_ref = runtime.artifacts.register(
        runtime.artifacts.resolve(value.media_ref, "media"), "media"
    )
    assert value.subtitle_ref
    value.subtitle_ref = runtime.artifacts.register(
        runtime.artifacts.resolve(value.subtitle_ref, "subtitle"), "subtitle"
    )
    assert value.burn_in_ref
    proof_path = runtime.artifacts.resolve(value.burn_in_ref, "burn_in")
    proof = BurnInProof.model_validate_json(proof_path.read_bytes())
    proof.ass_ref = runtime.artifacts.register(
        runtime.artifacts.resolve(proof.ass_ref, "subtitle_ass"), "subtitle_ass"
    )
    live_proof = proof_path.with_name("delivery-proof.json")
    live_proof.write_text(proof.model_dump_json(), encoding="utf-8")
    value.burn_in_ref = runtime.artifacts.register(live_proof, "burn_in")
    value.test_only, value.rights_confirmed = False, True
    prepared = runtime.publications.prepare(value)
    runtime.publications.confirm(prepared.id, prepared.manifest_hash)
    live = replace(runtime, settings=replace(runtime.settings, publication_enabled=True))
    gateway = ReceiptOnlyGateway()
    PublicationExecutor(live, gateway).submit_private(prepared.id)
    task = runtime.tasks.claim(("finalize_private",))
    assert task
    return task, gateway


@pytest.mark.parametrize("status", ["uploaded", "reviewing"])
def test_hard_delivery_finishes_without_any_review_read(
    runtime: Runtime, clock: Clock, assets: Assets, status: str
) -> None:
    task, gateway = delivered(runtime, clock, assets)
    request = FinalizePrivate.model_validate_json(task.input_json)
    with runtime.factory.begin() as db:
        runtime.publications.require(db, request.publication_id).status = status
    result = json.loads(PrivateFinalizer(runtime, gateway).run(task))
    assert result["completion_basis"] == "upload_receipt"
    assert result["status"] == "delivered" and not result["remote_review_checked"]
    assert gateway.writes == 1
    with runtime.factory() as db:
        assert runtime.publications.require(db, request.publication_id).status == "uploaded"


def test_unknown_delivery_is_not_finished_or_reuploaded(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    task, gateway = delivered(runtime, clock, assets)
    request = FinalizePrivate.model_validate_json(task.input_json)
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, request.publication_id)
        row.status, row.remote_id = "unknown", None
    with pytest.raises(WorkflowError, match="缺少明确投稿回执"):
        PrivateFinalizer(runtime, gateway).run(task)
    assert gateway.writes == 1


def test_delivery_receipt_allows_batch_cleanup_without_public_review(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    task, gateway = delivered(runtime, clock, assets)
    batch_id = "delivery-batch"
    with runtime.factory.begin() as db:
        db.add(
            Batch(
                id=batch_id,
                local_date=clock().date().isoformat(),
                snapshot_at=stamp(clock()),
                status="running",
                candidate_count=50,
                policy_json=runtime.policy.model_dump_json(),
                execution_json=BatchExecution(
                    batch_size=50, publications_per_batch=1, plan_revision=0, trigger="manual"
                ).model_dump_json(),
            )
        )
        row = db.get(Task, task.id)
        assert row
        row.batch_id = batch_id
    output = PrivateFinalizer(runtime, gateway).run(task)
    assert runtime.tasks.finish(task, output)
    refresh_batch_progress(runtime.factory, batch_id, clock())
    cleanup_task = runtime.tasks.claim(("cleanup_batch",))
    assert cleanup_task
    directory = runtime.artifacts.root / "production/test"
    directory.mkdir(parents=True)
    video, log = directory / "original.mp4", directory / "response.json"
    video.write_bytes(b"synthetic video")
    log.write_text("preserved LLM response", encoding="utf-8")
    cleanup_result = BatchMediaCleanup(runtime).run(cleanup_task)
    assert not video.exists() and log.read_text(encoding="utf-8") == "preserved LLM response"
    assert runtime.tasks.finish(cleanup_task, cleanup_result)
    refresh_batch_progress(runtime.factory, batch_id, clock())
    with runtime.factory() as db:
        batch = db.get(Batch, batch_id)
        assert batch and batch.status == "completed"
    assert gateway.writes == 1
