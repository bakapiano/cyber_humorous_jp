from dataclasses import replace

import pytest
from conftest import Assets, Clock
from test_publications import manifest
from test_remote import FakeGateway

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Publication


def test_private_lifecycle_with_fake_gateway_only(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    runtime.settings = replace(runtime.settings, publication_enabled=True)
    value = manifest(assets, clock)
    value.media_ref = runtime.artifacts.register(
        runtime.artifacts.resolve(assets.media, "media"), "media"
    )
    value.cover_ref = runtime.artifacts.register(
        runtime.artifacts.resolve(assets.cover, "cover"), "cover"
    )
    value.subtitle_ref = runtime.artifacts.register(
        runtime.artifacts.resolve(assets.subtitle, "subtitle"), "subtitle"
    )
    value.test_only, value.rights_confirmed = False, True
    prepared = runtime.publications.prepare(value)
    runtime.publications.confirm(prepared.id, prepared.manifest_hash)
    gateway = FakeGateway()
    executor = PublicationExecutor(runtime, gateway, legacy_native_subtitles=True)
    assert executor.submit_private(prepared.id) == "BV1234567890"
    with pytest.raises(WorkflowError):
        executor.submit_private(prepared.id)
    assert gateway.writes == 1
    assert executor.reconcile(prepared.id) == "awaiting_subtitles"
    assert executor.attach_subtitles(prepared.id) == "completed"
    assert executor.reconcile(prepared.id) == "completed"
    assert gateway.writes == 2


def test_subtitle_intent_cannot_be_claimed_twice(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    with runtime.factory.begin() as db:
        row = db.get(Publication, prepared.id)
        assert row is not None
        row.status = "uploaded"
    executor = PublicationExecutor(runtime, FakeGateway())
    executor.claim_subtitle_write(prepared.id)
    with pytest.raises(WorkflowError, match="重复写入"):
        executor.claim_subtitle_write(prepared.id)
