from dataclasses import replace
from pathlib import Path

import pytest
from conftest import Assets, Clock
from PIL import Image, ImageChops
from test_publications import manifest
from test_remote import FakeGateway

from youmo_workflow.adapters.editing_render import RenderReceipt, render_edit
from youmo_workflow.adapters.hard_subtitles import literal_ass, verify_burn_proof
from youmo_workflow.adapters.media import run_media
from youmo_workflow.domain.burn_in import BurnInProof
from youmo_workflow.domain.editing import TimelinePart
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.remote import RemoteSubmission
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime


def burned(runtime: Runtime, assets: Assets, clock: Clock) -> PublicationManifest:
    directory = runtime.artifacts.root / "burn-test"
    directory.mkdir()
    subtitle = directory / "reviewed.srt"
    subtitle.write_text(
        "1\n00:00:00,000 --> 00:00:01,900\n中文烧录检查\n日本語字幕テスト\n", encoding="utf-8"
    )
    timeline = [
        TimelinePart(part="main", source_start=0, source_end=2, output_start=0, output_end=2)
    ]
    target = render_edit(
        runtime.artifacts.resolve(assets.media, "media"), timeline, directory, subtitle
    )
    receipt = RenderReceipt.model_validate_json((directory / "render.json").read_bytes())
    assert receipt.burn_layout
    layout = receipt.burn_layout
    value = manifest(assets, clock)
    value.media_ref = runtime.artifacts.register(target, "media", test_only=True)
    value.subtitle_ref = runtime.artifacts.register(subtitle, "subtitle", test_only=True)
    proof = BurnInProof(
        media_sha256=receipt.sha256,
        subtitle_sha256=layout.subtitle_sha256,
        ass_ref=runtime.artifacts.register(
            directory / "burn-in.ass", "subtitle_ass", test_only=True
        ),
        font_sha256=layout.font_sha256,
        font_name=layout.font_name,
        width=layout.width,
        height=layout.height,
        cue_count=layout.cue_count,
        seconds=2,
    )
    path = directory / "proof.json"
    path.write_text(proof.model_dump_json(), encoding="utf-8")
    value.burn_in_ref = runtime.artifacts.register(path, "burn_in", test_only=True)
    value.subtitle_mode = "hard"
    return value


def test_real_bilingual_pixels_and_bound_proof(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    value = burned(runtime, assets, clock)
    runtime.publications.verifier.verify(value)
    assert verify_burn_proof(runtime.artifacts, value) == 2
    image_path = runtime.artifacts.root / "burn.png"
    media = runtime.artifacts.resolve(value.media_ref, "media")
    run_media(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "0.5",
            "-i",
            str(media),
            "-frames:v",
            "1",
            str(image_path),
        ],
        media.parent,
    )
    with Image.open(image_path) as image:
        lower = image.convert("RGB").crop((0, 90, 320, 180))
        background = Image.new("RGB", lower.size, lower.getpixel((0, 0)))
        assert ImageChops.difference(lower, background).getbbox() is not None
        assert sum(lower.convert("L").histogram()[160:]) > 60
    assert value.burn_in_ref and value.subtitle_ref
    assert value.media_ref.sha256 != assets.media.sha256
    forged = value.model_copy(update={"media_ref": assets.media})
    with pytest.raises(WorkflowError, match="未绑定"):
        verify_burn_proof(runtime.artifacts, forged)


class NoNativeGateway(FakeGateway):
    def __init__(self, duration: float):
        super().__init__()
        self.duration = duration

    def inspect(self, bvid: str) -> RemoteSubmission:
        remote = super().inspect(bvid)
        remote.videos[0].duration = self.duration
        return remote

    def verify_subtitles(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        raise AssertionError("Hard-subtitle publication must not query native tracks")

    def attach_and_verify(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        raise AssertionError("Hard-subtitle publication must not write native tracks")


def test_hard_publication_finishes_without_native_api(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    value = burned(runtime, assets, clock)
    prepared = runtime.publications.prepare(value)
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, prepared.id)
        row.status, row.remote_id = "uploaded", "BV1234567890"
    gateway = NoNativeGateway(2)
    executor = PublicationExecutor(runtime, gateway)
    assert executor.reconcile(prepared.id) == "completed"
    assert gateway.writes == 0
    gateway.duration = 20
    with pytest.raises(WorkflowError, match="时长"):
        executor.reconcile(prepared.id)
    with pytest.raises(WorkflowError, match="停用"):
        executor.attach_subtitles(prepared.id)


def test_native_uploads_disabled_by_default(runtime: Runtime, assets: Assets, clock: Clock) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    live = replace(runtime, settings=replace(runtime.settings, publication_enabled=True))
    gateway = FakeGateway()
    with pytest.raises(WorkflowError) as error:
        PublicationExecutor(live, gateway).attach_subtitles(prepared.id)
    assert error.value.code == "NATIVE_SUBTITLES_DISABLED" and gateway.writes == 0


def test_old_manifest_serialization_is_unchanged(assets: Assets, clock: Clock) -> None:
    old = manifest(assets, clock)
    assert "burn_in_ref" not in old.model_dump()
    assert (
        PublicationManifest.model_validate_json(old.model_dump_json()).model_dump_json()
        == old.model_dump_json()
    )
    assert literal_ass(r"{\alpha&HFF&}文本") == "｛＼alpha&HFF&｝文本"
