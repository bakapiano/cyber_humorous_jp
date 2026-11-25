import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from youmo_workflow.adapters.media import run_media
from youmo_workflow.adapters.quality import QualityGate
from youmo_workflow.config import Settings
from youmo_workflow.domain.models import DiscoveryVideo
from youmo_workflow.domain.providers import ArtifactRef
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.artifacts import ArtifactService
from youmo_workflow.features.batches import BatchService
from youmo_workflow.features.publications import PublicationService
from youmo_workflow.features.sources import SourceService
from youmo_workflow.features.tasks import TaskService
from youmo_workflow.runtime import Runtime, build_runtime
from youmo_workflow.storage.database import create_database
from youmo_workflow.storage.models import Base, Source


@dataclass
class Clock:
    at: datetime = datetime(2026, 11, 6, 3, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def runtime(tmp_path: Path, clock: Clock) -> Runtime:
    (tmp_path / "data").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "config").mkdir()
    shutil.copyfile(
        Path(__file__).parents[2] / "config/subtitle-denylist.json",
        tmp_path / "config/subtitle-denylist.json",
    )
    shutil.copyfile(
        Path(__file__).parents[2] / "config/deepseek-pricing.json",
        tmp_path / "config/deepseek-pricing.json",
    )
    settings = Settings(root=tmp_path, testing=True)
    engine = create_database(settings.database_url)
    Base.metadata.create_all(engine)
    current = build_runtime(settings, engine)
    current.artifacts = ArtifactService(current.factory, settings.artifact_root, clock)
    current.sources = SourceService(current.factory, clock)
    current.batches = BatchService(current.factory, clock)
    current.tasks = TaskService(current.factory, clock)
    current.publications = PublicationService(
        current.factory, QualityGate(current.artifacts), clock
    )
    with current.factory.begin() as db:
        db.add(
            Source(
                id="tbsnewsdig",
                name="TBS NEWS DIG",
                url="https://www.youtube.com/@tbsnewsdig",
                evidence_count=120,
                enabled=True,
            )
        )
    return current


def sample_video(clock: Clock, video_id: str = "abcdefghijk", hours: int = 2) -> DiscoveryVideo:
    return DiscoveryVideo(
        video_id=video_id,
        title="离线测试素材，不是真实新闻",
        channel_title="TBS NEWS DIG",
        channel_id="UC" + "a" * 22,
        published_at=stamp(clock.at - timedelta(hours=hours)),
    )


@dataclass
class Assets:
    media: ArtifactRef
    cover: ArtifactRef
    subtitle: ArtifactRef


@pytest.fixture
def assets(runtime: Runtime) -> Assets:
    root = runtime.settings.artifact_root
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required for the media acceptance tests")
    run_media(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=darkgreen:s=320x180:r=10",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(root / "test.mp4"),
        ],
        root,
    )
    Image.new("RGB", (320, 180), "darkgreen").save(root / "cover.jpg")
    (root / "test.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,900\n自产离线测试片\n", encoding="utf-8"
    )
    outputs: list[ArtifactRef] = []
    for name, kind in [
        ("test.mp4", "media"),
        ("cover.jpg", "cover"),
        ("test.srt", "subtitle"),
    ]:
        target = runtime.settings.artifact_root / name
        outputs.append(runtime.artifacts.register(target, kind, test_only=True))
    return Assets(*outputs)
