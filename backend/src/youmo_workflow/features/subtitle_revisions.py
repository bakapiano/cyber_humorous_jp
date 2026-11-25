import json

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.subtitle_revision import ActiveSubtitleRevision, SubtitleRevision
from youmo_workflow.storage.models import Publication, Task, WorkflowSetting

ACTIVE = "subtitle_revision_active:"
HISTORY = "subtitle_revision_record:"


def active_revision(db: Session, publication: Publication) -> SubtitleRevision | None:
    pointer = db.get(WorkflowSetting, ACTIVE + publication.id)
    if pointer is None:
        return None
    identifier = ActiveSubtitleRevision.model_validate_json(pointer.value_json).revision_id
    saved = db.get(WorkflowSetting, HISTORY + identifier)
    if saved is None:
        raise WorkflowError("SUBTITLE_REVISION_MISSING", "字幕修订记录缺失，拒绝继续。")
    revision = SubtitleRevision.model_validate_json(saved.value_json)
    manifest = PublicationManifest.model_validate_json(publication.manifest_json)
    if (
        revision.publication_id != publication.id
        or revision.manifest_hash != publication.manifest_hash
        or revision.remote_id != publication.remote_id
        or revision.base_ref != manifest.subtitle_ref
    ):
        raise WorkflowError("SUBTITLE_REVISION_CHANGED", "字幕修订与视频或原发布包不匹配。")
    return revision


def authorize_revision(factory: sessionmaker[Session], revision: SubtitleRevision) -> str:
    with factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        publication = db.get(Publication, revision.publication_id)
        if publication is None:
            raise WorkflowError("PUBLICATION_NOT_FOUND", "投稿记录不存在。")
        current = active_revision(db, publication)
        if current:
            if current == revision:
                return current.revision_id
            raise WorkflowError(
                "SUBTITLE_REVISION_LIMIT", "本轮额外修订额度已使用，不循环改词提交。"
            )
        manifest = PublicationManifest.model_validate_json(publication.manifest_json)
        if (
            publication.status != "subtitle_rejected"
            or publication.remote_id != revision.remote_id
            or publication.manifest_hash != revision.manifest_hash
            or publication.confirmed_hash != revision.manifest_hash
            or manifest.subtitle_ref != revision.previous_ref
            or revision.base_ref != manifest.subtitle_ref
            or revision.revised_ref.sha256 == revision.previous_ref.sha256
        ):
            raise WorkflowError(
                "SUBTITLE_REVISION_NOT_ELIGIBLE", "仅可修订已明确退回且正文有变化的字幕。"
            )
        task = db.scalar(
            select(Task).where(
                Task.unique_key == f"finalize:{publication.id}:{publication.manifest_hash}"
            )
        )
        if task is None or task.status != "failed":
            raise WorkflowError("SUBTITLE_REVISION_BUSY", "字幕收尾任务缺失或仍在执行，未重置。")
        expected = FinalizePrivate(
            publication_id=publication.id, manifest_hash=publication.manifest_hash
        )
        if FinalizePrivate.model_validate_json(task.input_json) != expected:
            raise WorkflowError("CONFIRMATION_STALE", "字幕任务与原发布包不匹配。")
        db.add(
            WorkflowSetting(
                id=HISTORY + revision.revision_id, revision=1, value_json=revision.model_dump_json()
            )
        )
        db.add(
            WorkflowSetting(
                id=ACTIVE + publication.id,
                revision=1,
                value_json=ActiveSubtitleRevision(
                    revision_id=revision.revision_id
                ).model_dump_json(),
            )
        )
        task.output_json = json.dumps(
            {
                "subtitle_revision": revision.revision_id,
                "previous_status": task.status,
                "previous_error_code": task.error_code,
                "previous_error_message": task.error_message,
                "previous_output": task.output_json,
                "attempts_preserved": task.attempts,
            }
        )
        task.status, task.next_run_at, task.updated_at = (
            "pending",
            revision.authorized_at,
            revision.authorized_at,
        )
        task.error_code = task.error_message = None
        task.lease_token = task.lease_until = None
        publication.status = "awaiting_subtitles"
        return revision.revision_id
