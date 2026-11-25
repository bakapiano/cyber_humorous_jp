from datetime import timedelta

import pytest
from conftest import Assets, Clock

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.time import stamp
from youmo_workflow.runtime import Runtime


def manifest(assets: Assets, clock: Clock) -> PublicationManifest:
    return PublicationManifest(
        content_id="test",
        title="自产测试",
        media_ref=assets.media,
        cover_ref=assets.cover,
        subtitle_ref=assets.subtitle,
        subtitle_mode="soft",
        source_url="https://www.youtube.com/watch?v=abcdefghijk",
        expires_at=stamp(clock() + timedelta(days=1)),
        test_only=True,
    )


def test_confirmation_required(runtime: Runtime, clock: Clock, assets: Assets) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    with pytest.raises(WorkflowError, match="尚未确认"):
        runtime.publications.plan(prepared.id)


def test_edit_invalidates_confirmation(runtime: Runtime, clock: Clock, assets: Assets) -> None:
    value = manifest(assets, clock)
    first = runtime.publications.prepare(value)
    runtime.publications.confirm(first.id, first.manifest_hash)
    value.title = "修改标题"
    second = runtime.publications.prepare(value)
    assert first.id == second.id and not second.confirmed
    with pytest.raises(WorkflowError):
        runtime.publications.confirm(first.id, first.manifest_hash)


def test_tampered_file_invalidates_plan(runtime: Runtime, clock: Clock, assets: Assets) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    runtime.publications.confirm(prepared.id, prepared.manifest_hash)
    path = runtime.artifacts.resolve(assets.subtitle, "subtitle")
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(WorkflowError, match="发生变化"):
        runtime.publications.plan(prepared.id)


def test_test_artifacts_cannot_be_real(runtime: Runtime, clock: Clock, assets: Assets) -> None:
    value = manifest(assets, clock)
    value.test_only = False
    with pytest.raises(WorkflowError, match="测试产物"):
        runtime.publications.prepare(value)


def test_expired_manifest_rejected(runtime: Runtime, clock: Clock, assets: Assets) -> None:
    value = manifest(assets, clock)
    value.expires_at = stamp(clock() - timedelta(seconds=1))
    with pytest.raises(WorkflowError, match="过期"):
        runtime.publications.prepare(value)


def test_path_traversal_rejected(runtime: Runtime) -> None:
    with pytest.raises(WorkflowError, match="越界"):
        runtime.artifacts.register(runtime.settings.artifact_root / "../escape.txt", "subtitle")
