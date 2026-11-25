import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.publication import PublicationDTO, PublicationManifest, SubmitPlan
from youmo_workflow.domain.quality import ManifestVerifier
from youmo_workflow.domain.time import now_utc, parse_stamp, stamp
from youmo_workflow.storage.models import Publication


def manifest_hash(manifest: PublicationManifest) -> str:
    serialized = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def publication_dto(row: Publication) -> PublicationDTO:
    manifest = PublicationManifest.model_validate_json(row.manifest_json)
    return PublicationDTO(
        id=row.id,
        content_id=row.content_id,
        title=manifest.title,
        manifest_hash=row.manifest_hash,
        confirmed=row.confirmed_hash == row.manifest_hash,
        status=row.status,
        visibility="private" if row.status == "withdrawn" else manifest.visibility,
        remote_id=row.remote_id,
        test_only=manifest.test_only,
        created_at=row.created_at,
    )


class PublicationService:
    def __init__(
        self,
        factory: sessionmaker[Session],
        verifier: ManifestVerifier,
        clock: Callable[[], datetime] = now_utc,
        sanitizer: Callable[[PublicationManifest], PublicationManifest] | None = None,
    ):
        self.factory = factory
        self.verifier = verifier
        self.clock = clock
        self.sanitizer = sanitizer

    def prepare(self, manifest: PublicationManifest) -> PublicationDTO:
        if self.sanitizer:
            manifest = self.sanitizer(manifest)
        self.verifier.verify(manifest)
        if parse_stamp(manifest.expires_at) <= self.clock():
            raise WorkflowError("CONTENT_EXPIRED", "内容已过期。")
        parsed = urlparse(manifest.source_url)
        if parsed.scheme != "https" or parsed.hostname not in {
            "youtube.com",
            "www.youtube.com",
            "youtu.be",
        }:
            raise WorkflowError("INVALID_SOURCE", "来源必须是有效的 YouTube HTTPS 链接。")
        digest = manifest_hash(manifest)
        with self.factory.begin() as db:
            existing = db.scalar(
                select(Publication).where(Publication.content_id == manifest.content_id)
            )
            if existing:
                return self.update_prepared(existing, manifest, digest)
            row = Publication(
                id=uuid4().hex,
                content_id=manifest.content_id,
                manifest_json=manifest.model_dump_json(),
                manifest_hash=digest,
                status="prepared",
                created_at=stamp(self.clock()),
            )
            db.add(row)
            db.flush()
            return publication_dto(row)

    def update_prepared(
        self, row: Publication, manifest: PublicationManifest, digest: str
    ) -> PublicationDTO:
        if row.manifest_hash == digest:
            return publication_dto(row)
        if row.status not in {"prepared", "confirmed"}:
            raise WorkflowError("PUBLICATION_IMMUTABLE", "已提交或结果不明的发布记录不可改写。")
        row.manifest_json = manifest.model_dump_json()
        row.manifest_hash = digest
        row.confirmed_hash = None
        row.status = "prepared"
        return publication_dto(row)

    def confirm(self, publication_id: str, digest: str) -> PublicationDTO:
        with self.factory.begin() as db:
            row = self.require(db, publication_id)
            if row.manifest_hash != digest or row.status not in {"prepared", "confirmed"}:
                raise WorkflowError("CONFIRMATION_STALE", "发布包已改变或已提交，不能确认旧版本。")
            row.confirmed_hash = digest
            row.status = "confirmed"
            return publication_dto(row)

    def require(self, db: Session, publication_id: str) -> Publication:
        row = db.get(Publication, publication_id)
        if row is None:
            raise WorkflowError("PUBLICATION_NOT_FOUND", "发布包不存在。")
        return row

    def list_publications(self) -> list[PublicationDTO]:
        with self.factory() as db:
            return [
                publication_dto(row)
                for row in db.scalars(
                    select(Publication).order_by(Publication.created_at.desc()).limit(100)
                )
            ]

    def plan(self, publication_id: str, *, allow_expired: bool = False) -> SubmitPlan:
        with self.factory() as db:
            row = self.require(db, publication_id)
            manifest = PublicationManifest.model_validate_json(row.manifest_json)
            if row.confirmed_hash != row.manifest_hash:
                raise WorkflowError("CONFIRMATION_REQUIRED", "发布包尚未确认。")
            if not allow_expired and parse_stamp(manifest.expires_at) <= self.clock():
                raise WorkflowError("CONTENT_EXPIRED", "内容已过期。")
            self.verifier.verify(manifest)
            return SubmitPlan(
                publication_id=row.id,
                title=manifest.title,
                visibility=manifest.visibility,
                manifest_hash=row.manifest_hash,
                media_verified=True,
                cover_verified=True,
                subtitle_verified=True,
                live_enabled=False,
            )
