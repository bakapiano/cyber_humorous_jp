from dataclasses import replace
from pathlib import Path

import pytest
from conftest import Assets, Clock
from test_publications import manifest
from test_remote import FakeGateway

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.remote import RemoteSubmission
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime


class RejectedAfterLookup(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def verify_subtitles(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        self.reads += 1
        if self.reads == 2:
            raise WorkflowError("SUBTITLE_REVIEW_REJECTED", "Synthetic rejection")
        return False


def test_rejection_between_reconcile_and_attach_remains_terminal(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, prepared.id)
        row.remote_id, row.status = "BV1234567890", "uploaded"
    live = replace(runtime, settings=replace(runtime.settings, publication_enabled=True))
    gateway = RejectedAfterLookup()
    executor = PublicationExecutor(live, gateway, legacy_native_subtitles=True)
    assert executor.reconcile(prepared.id) == "awaiting_subtitles"
    with pytest.raises(WorkflowError, match="Synthetic rejection"):
        executor.attach_subtitles(prepared.id)
    with runtime.factory() as db:
        assert runtime.publications.require(db, prepared.id).status == "subtitle_rejected"
    assert executor.reconcile(prepared.id) == "subtitle_rejected"
    with pytest.raises(WorkflowError, match="必须建立修订记录"):
        executor.attach_subtitles(prepared.id)
    assert gateway.writes == 0
