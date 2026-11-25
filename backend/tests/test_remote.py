from pathlib import Path

import pytest
from conftest import Assets, Clock
from test_publications import manifest

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.remote import RemoteArchive, RemotePart, RemoteSubmission, UploadFiles
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Publication


class FakeGateway:
    def __init__(self) -> None:
        self.writes = 0
        self.has_subtitle = False

    def preflight(self, manifest: PublicationManifest, files: UploadFiles) -> None:
        return None

    def submit(self, manifest: PublicationManifest, files: UploadFiles) -> str:
        self.writes += 1
        return "BV1234567890"

    def inspect(self, bvid: str) -> RemoteSubmission:
        return RemoteSubmission(
            archive=RemoteArchive(
                bvid=bvid,
                title="自产测试",
                aid=1,
                is_only_self=1,
                had_passed=True,
                state=0,
                copyright=2,
                source="https://www.youtube.com/watch?v=abcdefghijk",
            ),
            videos=[RemotePart(cid=1)],
        )

    def attach_and_verify(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        self.writes += 1
        self.has_subtitle = True
        return True

    def verify_subtitles(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        return self.has_subtitle


def test_live_disabled_before_gateway_access(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    gateway = FakeGateway()
    with pytest.raises(WorkflowError, match="未启用"):
        PublicationExecutor(runtime, gateway).submit_private(prepared.id)
    assert gateway.writes == 0


def test_reconcile_never_writes_subtitles(runtime: Runtime, clock: Clock, assets: Assets) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    with runtime.factory.begin() as db:
        row = db.get(Publication, prepared.id)
        assert row is not None
        row.remote_id, row.status = "BV1234567890", "uploaded"
    gateway = FakeGateway()
    executor = PublicationExecutor(runtime, gateway)
    assert executor.reconcile(prepared.id) == "awaiting_subtitles"
    assert gateway.writes == 0
    gateway.has_subtitle = True
    assert executor.reconcile(prepared.id) == "completed"
    assert gateway.writes == 0


def test_unknown_without_id_is_not_resubmitted(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    gateway = FakeGateway()
    with pytest.raises(WorkflowError, match="唯一远端"):
        PublicationExecutor(runtime, gateway).reconcile(prepared.id)
    assert gateway.writes == 0


class WrongCopyrightGateway(FakeGateway):
    def inspect(self, bvid: str) -> RemoteSubmission:
        result = super().inspect(bvid)
        result.archive.copyright = 1
        return result


def test_original_upload_is_not_accepted_as_verified_reprint(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, prepared.id)
        row.remote_id, row.status = "BV1234567890", "uploaded"
    gateway = WrongCopyrightGateway()
    with pytest.raises(WorkflowError, match="转载来源"):
        PublicationExecutor(runtime, gateway).reconcile(prepared.id)
    assert gateway.writes == 0
