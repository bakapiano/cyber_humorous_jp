import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import ArtifactRef
from youmo_workflow.domain.publication import ArtifactDTO
from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.storage.models import Artifact


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class ArtifactService:
    def __init__(
        self, factory: sessionmaker[Session], root: Path, clock: Callable[[], datetime] = now_utc
    ):
        self.factory = factory
        self.root = root.resolve()
        self.clock = clock

    def register(self, path: Path, kind: str, *, test_only: bool = False) -> ArtifactRef:
        resolved = self.safe_path(path)
        if kind not in {
            "media",
            "subtitle",
            "cover",
            "transcript",
            "edit_plan",
            "subtitle_ass",
            "burn_in",
        }:
            raise WorkflowError("INVALID_ARTIFACT_KIND", "产物类型不支持。")
        if not resolved.is_file() or resolved.stat().st_size == 0:
            raise WorkflowError("ARTIFACT_MISSING", "产物文件不存在或为空。")
        digest = file_hash(resolved)
        artifact_id = uuid4().hex
        with self.factory.begin() as db:
            db.add(
                Artifact(
                    id=artifact_id,
                    kind=kind,
                    relative_path=resolved.relative_to(self.root).as_posix(),
                    sha256=digest,
                    size_bytes=resolved.stat().st_size,
                    test_only=test_only,
                    created_at=stamp(self.clock()),
                )
            )
        return ArtifactRef(artifact_id=artifact_id, sha256=digest)

    def safe_path(self, path: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise WorkflowError("ARTIFACT_PATH_DENIED", "产物路径越界。")
        return resolved

    def get(self, artifact_id: str) -> Artifact:
        with self.factory() as db:
            row = db.get(Artifact, artifact_id)
            if row is None:
                raise WorkflowError("ARTIFACT_NOT_FOUND", "产物不存在。")
            db.expunge(row)
            return row

    def resolve(self, reference: ArtifactRef, expected_kind: str) -> Path:
        row = self.get(reference.artifact_id)
        if row.cleaned_at:
            raise WorkflowError("ARTIFACT_CLEANED", "视频已在投稿核验后清理，产物记录仍保留。")
        path = self.safe_path(self.root / row.relative_path)
        if row.kind != expected_kind or row.sha256 != reference.sha256:
            raise WorkflowError("ARTIFACT_VERSION_MISMATCH", "产物类型或版本不一致。")
        if not path.is_file() or file_hash(path) != row.sha256:
            raise WorkflowError("ARTIFACT_CHANGED", "产物文件已丢失或发生变化。")
        return path

    def describe(self, artifact_id: str) -> ArtifactDTO:
        row = self.get(artifact_id)
        return ArtifactDTO(
            artifact_id=row.id,
            kind=row.kind,
            sha256=row.sha256,
            size_bytes=row.size_bytes,
            test_only=row.test_only,
            available=row.cleaned_at is None,
        )
