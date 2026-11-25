"""Derive capacity from terminal video identities, without changing task or receipt state."""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from youmo_workflow.domain.cleanup import CleanupPlan
from youmo_workflow.domain.editing import EditedContent
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.models import VideoDTO
from youmo_workflow.domain.providers import ReadyContent, SelectedContent
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.storage.models import Publication, Task

ACTIVE = {"pending", "running", "retry_wait"}
TERMINAL = {"failed", "unknown"}
VIDEO_STAGES = {"prepare_video", "packaging", "editing", "prepare_publication", "publish"}
SOURCE_PREFIX = "https://www.youtube.com/watch?v="


def released_video_slots(db: Session, tasks: list[Task], skips: set[str]) -> int:
    terminal, active, cleaned = set[str](), set[str](), set[str]()
    anonymous_cleanups = 0
    for task in tasks:
        if task.kind == "cleanup_publication" and task.status == "succeeded":
            video_id = cleanup_video(db, task)
            if video_id:
                cleaned.add(video_id)
            else:
                anonymous_cleanups += 1  # Preserve older successful cleanup accounting.
        elif task.kind in VIDEO_STAGES and task.status in TERMINAL | ACTIVE:
            video_id = stage_video(db, task)
            if video_id:
                (terminal if task.status in TERMINAL else active).add(video_id)
    return anonymous_cleanups + len(skips | cleaned | (terminal - active))


def stage_video(db: Session, task: Task) -> str | None:
    try:
        if task.kind == "prepare_video":
            return VideoDTO.model_validate_json(task.input_json).video_id
        if task.kind == "packaging":
            return SelectedContent.model_validate_json(task.input_json).video_id
        if task.kind == "editing":
            return ReadyContent.model_validate_json(task.input_json).content.video_id
        if task.kind == "prepare_publication":
            return EditedContent.model_validate_json(task.input_json).content.video_id
        request = FinalizePrivate.model_validate_json(task.input_json)
        publication = db.get(Publication, request.publication_id)
        if publication and publication.manifest_hash == request.manifest_hash:
            if task.status in ACTIVE or (
                not publication.remote_id and publication.status != "submitting"
            ):
                return publication_video(publication)
    except ValidationError:
        pass  # An unidentifiable failure is not evidence to release a slot.
    return None


def cleanup_video(db: Session, task: Task) -> str | None:
    try:
        plan = CleanupPlan.model_validate_json(task.input_json)
        publication = db.get(Publication, plan.publication_id) if plan.publication_id else None
        return publication_video(publication) if publication else None
    except ValidationError:
        return None


def publication_video(publication: Publication) -> str | None:
    source = PublicationManifest.model_validate_json(publication.manifest_json).source_url
    return source.removeprefix(SOURCE_PREFIX) if source.startswith(SOURCE_PREFIX) else None


def terminal_unbound_package(db: Session, publication: Publication) -> bool:
    if publication.status in {"withdrawn", "cancelled", *TERMINAL}:
        return True
    if publication.remote_id or publication.status == "submitting":
        return False
    stages = set(
        db.scalars(
            select(Task.status).where(
                Task.unique_key.in_(
                    [f"prepare:{publication.content_id}", f"batch-publish:{publication.id}"]
                )
            )
        )
    )
    return bool(stages & TERMINAL and not stages & ACTIVE)
