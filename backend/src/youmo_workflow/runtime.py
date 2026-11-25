from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.adapters.quality import QualityGate
from youmo_workflow.adapters.subtitle_review import SubtitleReviewer
from youmo_workflow.config import Settings
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.features.artifacts import ArtifactService
from youmo_workflow.features.batches import BatchService
from youmo_workflow.features.publications import PublicationService
from youmo_workflow.features.sources import SourceService, seed_sources
from youmo_workflow.features.tasks import TaskService
from youmo_workflow.storage.database import create_database, migrate, sessions


@dataclass
class Runtime:
    settings: Settings
    engine: Engine
    factory: sessionmaker[Session]
    policy: Policy
    artifacts: ArtifactService
    sources: SourceService
    batches: BatchService
    tasks: TaskService
    publications: PublicationService
    subtitle_check: Callable[[Path], None] | None = None
    hard_subtitle_check: Callable[[PublicationManifest], float] | None = None


def build_runtime(settings: Settings, engine: Engine | None = None) -> Runtime:
    policy_path = settings.root / "config/policy.json"
    policy = (
        Policy.model_validate_json(policy_path.read_bytes()) if policy_path.is_file() else Policy()
    )
    actual_engine = engine if engine is not None else create_database(settings.database_url)
    factory = sessions(actual_engine)
    artifacts = ArtifactService(factory, settings.artifact_root)
    reviewer = SubtitleReviewer(artifacts, settings.root / "config/subtitle-denylist.json")
    quality = QualityGate(artifacts, reviewer.verify)
    return Runtime(
        settings=settings,
        engine=actual_engine,
        factory=factory,
        policy=policy,
        artifacts=artifacts,
        sources=SourceService(factory),
        batches=BatchService(factory),
        tasks=TaskService(factory),
        publications=PublicationService(factory, quality, sanitizer=reviewer.prepare),
        subtitle_check=reviewer.verify,
        hard_subtitle_check=quality.verify_burn,
    )


def initialize(runtime: Runtime) -> None:
    (runtime.settings.root / "data").mkdir(parents=True, exist_ok=True)
    runtime.settings.artifact_root.mkdir(parents=True, exist_ok=True)
    migrate(runtime.engine)
    seed_sources(runtime.factory, runtime.settings.source_manifest)
