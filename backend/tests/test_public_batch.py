from pathlib import Path

import pytest
from batch_pipeline_support import RecordingGateway, batch_runner, offline_live_assets
from conftest import Assets, Clock
from selection_support import discovery_items
from test_batch_trigger import configure, manual
from test_publications import manifest

from youmo_workflow.adapters.biliup import BiliupGateway, receipt_matches
from youmo_workflow.domain.batch_plan import BatchPlanUpdate
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.remote import UploadFiles
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.batch_trigger import BatchTriggerService
from youmo_workflow.features.batch_visibility import authorize_unsubmitted_public, visibility
from youmo_workflow.orchestration.publication import verify_remote_identity
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch


@pytest.mark.parametrize("supplemental", [False, True])
def test_public_pipeline_matches_explicit_authorization(
    runtime: Runtime, clock: Clock, assets: Assets, supplemental: bool
) -> None:
    revision = configure(runtime, clock, count=1)
    service = BatchPlanService(runtime.factory, runtime.policy, clock)
    if not supplemental:
        revision = service.update(
            BatchPlanUpdate(
                revision=revision,
                batch_size=50,
                publications_per_batch=1,
                publication_mode="public_reprint",
            )
        ).revision
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(revision))
    assert batch
    with runtime.factory() as db:
        original = db.get(Batch, batch.id)
        assert original
        snapshot = original.execution_json
    if supplemental:
        authorize_unsubmitted_public(
            runtime.factory, batch.id, "User explicitly requested public uploads"
        )
        authorize_unsubmitted_public(runtime.factory, batch.id, "Idempotent replay")
    configure(runtime, clock, count=2)
    gateway = RecordingGateway()
    runner = batch_runner(runtime, clock, offline_live_assets(runtime, assets), gateway)
    for _ in range(20):
        if not runner.once():
            break
    assert runtime.batches.detail(batch.id).status == "completed", runtime.tasks.list_tasks()
    assert len(gateway.uploads) == 1 and gateway.writes == 2
    assert all(value.visibility == "public" for value in gateway.uploads.values())
    assert runtime.publications.list_publications()[0].visibility == "public"
    with runtime.factory() as db:
        original = db.get(Batch, batch.id)
        assert original and original.execution_json == snapshot
        assert visibility(db, original) == "public"


def test_supplement_cannot_convert_already_bound_private_batch(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    revision = configure(runtime, clock, count=1)
    runtime.sources.ingest("tbsnewsdig", discovery_items(50), stamp(clock()))
    batch = BatchTriggerService(runtime.factory, runtime.policy, clock).trigger(manual(revision))
    assert batch
    gateway = RecordingGateway()
    runner = batch_runner(runtime, clock, offline_live_assets(runtime, assets), gateway)
    for _ in range(20):
        if not runner.once():
            break
    with pytest.raises(WorkflowError, match="不自动更改"):
        authorize_unsubmitted_public(runtime.factory, batch.id, "Do not edit existing uploads")
    assert all(value.visibility == "private" for value in gateway.uploads.values())


def test_public_upload_command_and_receipt_require_exact_visibility(
    assets: Assets, clock: Clock
) -> None:
    value = manifest(assets, clock)
    value.visibility, value.test_only, value.rights_confirmed = "public", False, True
    gateway = BiliupGateway(Path("synthetic.exe"), Path("synthetic.json"))
    files = UploadFiles(media=Path("fake.mp4"), cover=Path("fake.jpg"), subtitle=None)
    command = gateway.upload_command(value, files)
    assert command[command.index("--rust-log") + 1] == "warn,biliup::uploader::bilibili=info"
    assert command.index("--rust-log") < command.index("upload")
    assert command[command.index("--is-only-self") + 1] == "0"
    assert command[command.index("--copyright") + 1] == "2"
    assert command[command.index("--source") + 1] == value.source_url
    with pytest.raises(WorkflowError, match="不能发布公开"):
        gateway.private_command(value, files)
    remote = RecordingGateway()
    bvid = remote.submit(value, files)
    receipt = remote.inspect(bvid)
    receipt.archive.ctime = 1
    verify_remote_identity(value, receipt)
    assert receipt_matches(receipt, value, 1, 2)
    receipt.archive.is_only_self = 1
    assert not receipt_matches(receipt, value, 1, 2)
    with pytest.raises(WorkflowError, match="可见性"):
        verify_remote_identity(value, receipt)
