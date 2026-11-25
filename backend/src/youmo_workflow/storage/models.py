from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    url: Mapped[str]
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_checked: Mapped[str | None]
    sync_boundary: Mapped[str | None]
    last_error: Mapped[str | None]


class Video(Base):
    __tablename__ = "videos"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    title: Mapped[str]
    channel_id: Mapped[str | None]
    channel_title: Mapped[str | None]
    published_at: Mapped[str | None] = mapped_column(String, index=True)
    duration_seconds: Mapped[int | None]
    thumbnail_url: Mapped[str | None]
    origin: Mapped[str] = mapped_column(String, default="channel")
    discovered_at: Mapped[str]
    revision: Mapped[int] = mapped_column(Integer, default=1)
    screened: Mapped[bool] = mapped_column(Boolean, default=False)


class Batch(Base):
    __tablename__ = "production_batches"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    local_date: Mapped[str] = mapped_column(String, index=True)
    snapshot_at: Mapped[str]
    status: Mapped[str] = mapped_column(String, default="queued")
    policy_json: Mapped[str] = mapped_column(Text)
    candidate_count: Mapped[int]
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    run_key: Mapped[str | None] = mapped_column(String, unique=True)
    execution_json: Mapped[str | None] = mapped_column(Text)
    reservations_released: Mapped[bool] = mapped_column(Boolean, default=False)


class BatchItem(Base):
    __tablename__ = "batch_items"
    batch_id: Mapped[str] = mapped_column(ForeignKey("production_batches.id"), primary_key=True)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id"), primary_key=True)
    snapshot_json: Mapped[str] = mapped_column(Text)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    unique_key: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[str]
    batch_id: Mapped[str | None]
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    output_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_run_at: Mapped[str]
    lease_token: Mapped[str | None]
    lease_until: Mapped[str | None]
    error_code: Mapped[str | None]
    error_message: Mapped[str | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str]
    relative_path: Mapped[str]
    sha256: Mapped[str]
    size_bytes: Mapped[int]
    test_only: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str]
    cleaned_at: Mapped[str | None]


class Publication(Base):
    __tablename__ = "publications"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    content_id: Mapped[str] = mapped_column(String, unique=True)
    manifest_json: Mapped[str] = mapped_column(Text)
    manifest_hash: Mapped[str]
    confirmed_hash: Mapped[str | None]
    status: Mapped[str]
    remote_id: Mapped[str | None]
    created_at: Mapped[str]


class PublishSlot(Base):
    __tablename__ = "publish_slots"
    __table_args__ = (UniqueConstraint("local_date", "slot_time"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    local_date: Mapped[str]
    slot_time: Mapped[str]
    publication_id: Mapped[str] = mapped_column(ForeignKey("publications.id"), unique=True)


class WorkflowSetting(Base):
    __tablename__ = "workflow_settings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    revision: Mapped[int]
