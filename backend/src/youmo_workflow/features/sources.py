from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import DiscoveryVideo, SourceDTO, VideoDTO, VideoPage
from youmo_workflow.domain.time import now_utc, parse_stamp, stamp
from youmo_workflow.storage.models import Source, Video


class CatalogSource(BaseModel):
    name: str
    url: str
    bilibili_video_count: int
    channel_id: str | None = None


def known_channel_ids(manifest: Path) -> dict[str, str]:
    if not manifest.is_file():
        return {}
    entries = TypeAdapter(list[CatalogSource]).validate_json(manifest.read_bytes())
    return {
        entry.channel_id: entry.url.rstrip("/").split("/")[-1].removeprefix("@")
        for entry in entries
        if entry.channel_id
    }


def seed_sources(factory: sessionmaker[Session], manifest: Path) -> int:
    if not manifest.is_file():
        return 0
    entries = TypeAdapter(list[CatalogSource]).validate_json(manifest.read_bytes())
    enabled = {"tbsnewsdig", "ntv_news", "ANNnewsCH"}
    with factory.begin() as db:
        for entry in entries:
            source_id = entry.url.rstrip("/").split("/")[-1].removeprefix("@")
            if db.get(Source, source_id) is None:
                db.add(
                    Source(
                        id=source_id,
                        name=entry.name,
                        url=entry.url,
                        evidence_count=entry.bilibili_video_count,
                        enabled=source_id in enabled,
                    )
                )
    return len(entries)


def video_dto(row: Video) -> VideoDTO:
    return VideoDTO.model_validate(
        {
            "video_id": row.id,
            "source_id": row.source_id,
            "title": row.title,
            "channel_id": row.channel_id,
            "channel_title": row.channel_title,
            "published_at": row.published_at,
            "duration_seconds": row.duration_seconds,
            "thumbnail_url": row.thumbnail_url,
            "origin": row.origin,
            "discovered_at": row.discovered_at,
            "revision": row.revision,
            "screened": row.screened,
        }
    )


class SourceService:
    def __init__(self, factory: sessionmaker[Session], clock: Callable[[], datetime] = now_utc):
        self.factory = factory
        self.clock = clock

    def list_sources(self) -> list[SourceDTO]:
        with self.factory() as db:
            counts = dict(
                db.execute(select(Video.source_id, func.count()).group_by(Video.source_id)).all()
            )
            rows = db.scalars(
                select(Source).order_by(Source.enabled.desc(), Source.evidence_count.desc())
            )
            return [
                SourceDTO(
                    id=s.id,
                    name=s.name,
                    url=s.url,
                    evidence_count=s.evidence_count,
                    enabled=s.enabled,
                    last_checked=s.last_checked,
                    last_error=s.last_error,
                    video_count=int(counts.get(s.id, 0)),
                )
                for s in rows
            ]

    def set_enabled(self, source_id: str, enabled: bool) -> None:
        with self.factory.begin() as db:
            row = db.get(Source, source_id)
            if row is None:
                raise WorkflowError("SOURCE_NOT_FOUND", "来源不存在。")
            row.enabled = enabled

    def list_videos(self, source_id: str, offset: int, limit: int) -> VideoPage:
        with self.factory() as db:
            query = select(Video)
            if source_id:
                query = query.where(Video.source_id == source_id)
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = db.scalars(query.order_by(Video.published_at.desc()).offset(offset).limit(limit))
            return VideoPage(items=[video_dto(row) for row in rows], total=total)

    def ingest(
        self,
        source_id: str,
        items: list[DiscoveryVideo],
        boundary: str,
        *,
        complete_scan: bool = True,
    ) -> int:
        new_count = 0
        with self.factory.begin() as db:
            source = db.get(Source, source_id)
            if source is None:
                raise WorkflowError("SOURCE_NOT_FOUND", "来源不存在。")
            for item in items:
                new_count += upsert_video(db, source_id, item, stamp(self.clock()))
            if complete_scan:
                source.last_checked = stamp(self.clock())
                source.sync_boundary = boundary
                source.last_error = None
        return new_count

    def record_failure(self, source_id: str, message: str) -> None:
        with self.factory.begin() as db:
            source = db.get(Source, source_id)
            if source:
                source.last_error = message

    def ingest_recommendations(
        self, items: list[DiscoveryVideo], known: dict[str, str]
    ) -> dict[str, int]:
        groups: dict[str, list[DiscoveryVideo]] = {}
        for item in items:
            source_id = known.get(item.channel_id or "")
            if source_id:
                groups.setdefault(source_id, []).append(item)
        added = sum(
            self.ingest(source_id, group, stamp(self.clock()), complete_scan=False)
            for source_id, group in groups.items()
        )
        accepted = sum(len(group) for group in groups.values())
        return {
            "new_videos": added,
            "accepted": accepted,
            "outside_reference_sources": len(items) - accepted,
        }


def upsert_video(db: Session, source_id: str, item: DiscoveryVideo, at: str) -> int:
    published = stamp(parse_stamp(item.published_at)) if item.published_at else None
    old = db.get(Video, item.video_id)
    values = item.model_dump(exclude={"video_id", "published_at"})
    if old is None:
        db.add(
            Video(
                id=item.video_id,
                source_id=source_id,
                discovered_at=at,
                published_at=published,
                **values,
            )
        )
        return 1
    duration = item.duration_seconds if item.duration_seconds is not None else old.duration_seconds
    if old.title != item.title or old.published_at != published or old.duration_seconds != duration:
        old.revision += 1
    old.title = item.title
    old.published_at = published
    old.thumbnail_url = item.thumbnail_url
    old.duration_seconds = duration
    return 0
