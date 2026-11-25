import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from batch_pipeline_support import offline_live_assets
from conftest import Assets, Clock
from test_biliup_receipts import credentials
from test_publications import manifest
from test_remote import FakeGateway

from youmo_workflow.adapters.biliup import BiliupGateway, preupload_rate_limited
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.remote import UploadFiles
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.publication_cooldown import MINIMUM_GAP, active_until, record_rejection
from youmo_workflow.features.tasks import add_task
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime

RATE = "Error: after retries\n at crates\\biliup-cli\\src\\uploader.rs:579:14\n upload rate limit (code: 601): synthetic rejection"


class RejectedGateway(FakeGateway):
    code = "BILI_PREUPLOAD_RATE_LIMIT"

    def submit(self, manifest: PublicationManifest, files: UploadFiles) -> str:
        self.writes += 1
        raise WorkflowError(self.code, "synthetic failure", retryable=True)


def test_exact_preupload_rejection_is_recognized_before_receipt_lookup(
    tmp_path: Path, assets: Assets, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, private = tmp_path / "synthetic.exe", tmp_path / "private.json"
    executable.write_bytes(b"not executed")
    credentials(private)
    calls: list[int] = []

    def run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        return subprocess.CompletedProcess([], 1, "", RATE)

    monkeypatch.setattr(subprocess, "run", run)
    value = manifest(assets, clock).model_copy(
        update={"test_only": False, "rights_confirmed": True}
    )
    with pytest.raises(WorkflowError) as error:
        BiliupGateway(executable, private).submit(
            value,
            UploadFiles(media=tmp_path / "source.mp4", cover=tmp_path / "cover.jpg", subtitle=None),
        )
    assert error.value.code == "BILI_PREUPLOAD_RATE_LIMIT" and error.value.retryable
    assert len(calls) == 1
    assert not preupload_rate_limited("upload rate limit (code: 601)")
    assert not preupload_rate_limited(RATE.replace(":579:", ":999:"))


def test_known_rejection_keeps_bound_manifest_and_defers_account(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    value = manifest(offline_live_assets(runtime, assets), clock)
    value.test_only, value.rights_confirmed = False, True
    prepared = runtime.publications.prepare(value)
    runtime.publications.confirm(prepared.id, prepared.manifest_hash)
    live = replace(runtime, settings=replace(runtime.settings, publication_enabled=True))
    gateway = RejectedGateway()
    with pytest.raises(WorkflowError, match="synthetic failure"):
        PublicationExecutor(live, gateway).submit(prepared.id)
    with runtime.factory() as db:
        row = runtime.publications.require(db, prepared.id)
        assert row.status == "queued" and row.remote_id is None
        assert row.confirmed_hash == prepared.manifest_hash
        assert active_until(db, clock()) == stamp(clock() + MINIMUM_GAP)
    with pytest.raises(WorkflowError) as error:
        PublicationExecutor(live, gateway).submit(prepared.id)
    assert error.value.code == "BILI_UPLOAD_COOLDOWN" and gateway.writes == 1


def test_cooldown_skips_publish_but_not_scanning(runtime: Runtime, clock: Clock) -> None:
    with runtime.factory.begin() as db:
        record_rejection(db, clock())
        publish = add_task(db, "publish", "synthetic-publish", stamp(clock()))
        add_task(db, "sync_source", "synthetic-sync", stamp(clock()))
        publication_task = publish.id
    scan = runtime.tasks.claim()
    assert scan and scan.kind == "sync_source"
    runtime.tasks.finish(scan)
    assert runtime.tasks.claim() is None
    clock.at += timedelta(minutes=31)
    claimed = runtime.tasks.claim()
    assert claimed and claimed.id == publication_task


def test_other_write_errors_stay_unknown(runtime: Runtime, assets: Assets, clock: Clock) -> None:
    value = manifest(offline_live_assets(runtime, assets), clock)
    value.test_only, value.rights_confirmed = False, True
    prepared = runtime.publications.prepare(value)
    runtime.publications.confirm(prepared.id, prepared.manifest_hash)
    live = replace(runtime, settings=replace(runtime.settings, publication_enabled=True))
    gateway = RejectedGateway()
    gateway.code = "UNVERIFIED_RATE_LIMIT"
    with pytest.raises(WorkflowError) as error:
        PublicationExecutor(live, gateway).submit(prepared.id)
    assert error.value.code == "SUBMIT_RESULT_UNKNOWN"
    with runtime.factory() as db:
        assert runtime.publications.require(db, prepared.id).status == "unknown"
        assert active_until(db, clock()) == stamp(clock() + MINIMUM_GAP)
