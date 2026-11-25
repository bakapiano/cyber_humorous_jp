from datetime import timedelta
from pathlib import Path

from conftest import Assets, Clock
from selection_support import FixtureInitial
from test_pipeline import FixtureEditing, FixturePackaging
from test_remote import FakeGateway
from test_selection_stages import make_runner

from youmo_workflow.domain.models import VideoDTO
from youmo_workflow.domain.providers import SelectedContent
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.remote import RemoteArchive, RemotePart, RemoteSubmission, UploadFiles
from youmo_workflow.domain.selection import InitialOutput, InitialRequest
from youmo_workflow.domain.time import stamp
from youmo_workflow.orchestration.batch_publication import BatchPublisher
from youmo_workflow.orchestration.cleanup import BatchMediaCleanup
from youmo_workflow.orchestration.finalization import PrivateFinalizer
from youmo_workflow.orchestration.runner import Runner
from youmo_workflow.runtime import Runtime


class FixtureV008(FixtureInitial):
    def rank(self, request: InitialRequest) -> InitialOutput:
        return super().rank(request).model_copy(update={"version": "v008"})


class FixtureSingleContent:
    def __init__(self, assets: Assets, clock: Clock) -> None:
        self.assets, self.clock = assets, clock
        self.calls: list[str] = []

    def prepare_video(self, video: VideoDTO) -> SelectedContent:
        self.calls.append(video.video_id)
        return SelectedContent(
            content_id=f"synthetic-{video.video_id}",
            video_id=video.video_id,
            original_title=video.title,
            original_media_ref=self.assets.media,
            transcript_ref=self.assets.subtitle,
            translation_ref=self.assets.subtitle,
            evidence=["自产隔离测试视频"],
            expires_at=stamp(self.clock() + timedelta(days=1)),
        )


class RecordingGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.uploads: dict[str, PublicationManifest] = {}
        self.subtitles: set[str] = set()

    def submit(self, manifest: PublicationManifest, files: UploadFiles) -> str:
        assert manifest.rights_confirmed
        self.writes += 1
        bvid = f"BV{len(self.uploads):010}"
        self.uploads[bvid] = manifest
        return bvid

    def inspect(self, bvid: str) -> RemoteSubmission:
        manifest = self.uploads[bvid]
        return RemoteSubmission(
            archive=RemoteArchive(
                bvid=bvid,
                title=manifest.title,
                aid=1,
                is_only_self=int(manifest.visibility == "private"),
                copyright=2,
                source=manifest.source_url,
                had_passed=True,
            ),
            videos=[RemotePart(cid=1, duration=2)],
        )

    def attach_and_verify(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        self.writes += 1
        self.subtitles.add(submission.archive.bvid)
        return True

    def verify_subtitles(self, submission: RemoteSubmission, subtitle: Path) -> bool:
        return submission.archive.bvid in self.subtitles


def offline_live_assets(runtime: Runtime, assets: Assets) -> Assets:
    # Only for the fake gateway: simulate non-test production metadata using
    # synthetic local media, never configured against an external account.
    return Assets(
        runtime.artifacts.register(runtime.artifacts.resolve(assets.media, "media"), "media"),
        runtime.artifacts.register(runtime.artifacts.resolve(assets.cover, "cover"), "cover"),
        runtime.artifacts.register(
            runtime.artifacts.resolve(assets.subtitle, "subtitle"), "subtitle"
        ),
    )


def batch_runner(runtime: Runtime, clock: Clock, assets: Assets, gateway: FakeGateway) -> Runner:
    runner = make_runner(runtime, clock)
    assert runner.selection_stages is not None
    runner.selection_stages.providers.v008 = FixtureV008()
    runner.providers.content = FixtureSingleContent(assets, clock)
    runner.providers.packaging = FixturePackaging(assets)
    runner.providers.editor = FixtureEditing(runtime, assets)
    publisher = BatchPublisher(runtime, gateway)
    runner.authorize_publication, runner.publish_private = publisher.authorize, publisher.run
    runner.finalize_private = PrivateFinalizer(runtime, gateway, legacy_native_subtitles=True).run
    runner.cleanup_batch = BatchMediaCleanup(runtime).run
    return runner
