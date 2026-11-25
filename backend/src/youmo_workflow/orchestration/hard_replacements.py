"""Owner handles old-post deletion; new immutable uploads use the normal batch publisher."""

import hashlib
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from youmo_workflow.domain.editing import EditedContent
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.hard_replacement import (
    HARD_REPLACEMENT_PREFIX,
    HardReplacement,
    HardReplacementRequest,
)
from youmo_workflow.domain.providers import ReadyContent
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.tasks import add_task
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Publication, Task, WorkflowSetting


def stage_hard_replacement(
    runtime: Runtime, request: HardReplacementRequest, at: datetime
) -> HardReplacement:
    if not request.owner_manages_removal:
        raise WorkflowError(
            "REPLACEMENT_AUTHORIZATION_REQUIRED", "没有用户自行删除旧稿的明确分工授权。"
        )
    digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    with runtime.factory() as db:
        existing = db.get(WorkflowSetting, HARD_REPLACEMENT_PREFIX + request.new_content_id)
        if existing:
            record = HardReplacement.model_validate_json(existing.value_json)
            if record.request_sha256 != digest:
                raise WorkflowError("REPLACEMENT_CHANGED", "已有不同的替换请求，未覆盖或重排。")
            return record
        source = db.get(Task, request.source_task_id)
        if source is None or source.kind != "editing" or not source.batch_id:
            raise WorkflowError("REPLACEMENT_SOURCE_MISSING", "没有对应的历史剪辑输入。")
        ready = ReadyContent.model_validate_json(source.input_json)
        batch_id = source.batch_id
    verify_output(runtime, request, ready)
    source_url = f"https://www.youtube.com/watch?v={ready.content.video_id}"
    with runtime.factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if db.get(WorkflowSetting, HARD_REPLACEMENT_PREFIX + request.new_content_id):
            raise WorkflowError("REPLACEMENT_BUSY", "替换请求已被另一执行器登记，未重复排队。")
        old = bound_old_publications(db, request, source, source_url)
        aliased = EditedContent(
            content=ready.content.model_copy(update={"content_id": request.new_content_id}),
            packaging=ready.packaging.model_copy(update={"content_id": request.new_content_id}),
            editing=request.editing.model_copy(update={"content_id": request.new_content_id}),
        )
        task = add_task(db, "prepare_publication", f"prepare:{request.new_content_id}", stamp(at))
        task.batch_id, task.input_json = batch_id, aliased.model_dump_json()
        record = HardReplacement(
            request_sha256=digest,
            source_task_id=source.id,
            prepare_task_id=task.id,
            batch_id=batch_id,
            new_content_id=request.new_content_id,
            reused_content_id=ready.content.content_id,
            video_id=ready.content.video_id,
            old_manifests={row.id: row.manifest_hash for row in old},
            old_remote_ids=sorted(row.remote_id for row in old if row.remote_id),
            media_sha256=request.editing.media_ref.sha256,
            subtitle_sha256=request.editing.subtitle_ref.sha256,
            authority="用户：我来删除，你负责上传新的；公开转载，旧稿删除由用户完成，不把旧记录伪装成已删除。",
            authorized_at=stamp(at),
        )
        db.add(
            WorkflowSetting(
                id=HARD_REPLACEMENT_PREFIX + request.new_content_id,
                revision=1,
                value_json=record.model_dump_json(),
            )
        )
        return record


def bound_old_publications(
    db: Session, request: HardReplacementRequest, source: Task, source_url: str
) -> list[Publication]:
    current_source = db.get(Task, source.id)
    if (
        current_source is None
        or current_source.input_json != source.input_json
        or current_source.batch_id != source.batch_id
    ):
        raise WorkflowError("REPLACEMENT_SOURCE_CHANGED", "历史剪辑输入已变化，不创建替换稿。")
    if db.scalar(
        select(Publication.id).where(Publication.content_id == request.new_content_id)
    ) or db.scalar(select(Task.id).where(Task.unique_key == f"prepare:{request.new_content_id}")):
        raise WorkflowError("REPLACEMENT_ID_REUSED", "新版标识已有不匹配记录，不覆盖。")
    old = list(
        db.scalars(
            select(Publication).where(
                func.json_extract(Publication.manifest_json, "$.source_url") == source_url
            )
        )
    )
    if not old or {row.id for row in old} != set(request.old_publication_ids):
        raise WorkflowError("REPLACEMENT_SET_CHANGED", "同源发布记录不完整或已有新版，先对账。")
    if any(not row.remote_id or row.status in {"unknown", "submitting", "queued"} for row in old):
        raise WorkflowError(
            "REPLACEMENT_UNKNOWN_WRITE", "该来源存在未知或未完成投稿，不创建可能重复的新稿。"
        )
    if any(row.confirmed_hash != row.manifest_hash for row in old):
        raise WorkflowError("REPLACEMENT_UNCONFIRMED", "旧稿缺少匹配的确认摘要。")
    return old


def verify_output(runtime: Runtime, request: HardReplacementRequest, ready: ReadyContent) -> None:
    output = request.editing
    if (
        output.content_id != ready.content.content_id
        or output.title != ready.packaging.title
        or output.original_media_ref != ready.content.original_media_ref
        or output.subtitle_mode != "hard"
        or output.burn_in_ref is None
        or output.version != "v004_hardsub"
        or ready.packaging.packaging_version != "raw_history_poster_v001"
    ):
        raise WorkflowError(
            "REPLACEMENT_OUTPUT_INVALID", "新版输出未绑定原始输入或不是新版硬字幕。"
        )
    runtime.artifacts.resolve(output.plan_ref, "edit_plan")
    runtime.publications.verifier.verify(
        PublicationManifest(
            content_id=request.new_content_id,
            title=output.title,
            source_url=f"https://www.youtube.com/watch?v={ready.content.video_id}",
            media_ref=output.media_ref,
            subtitle_ref=output.subtitle_ref,
            subtitle_mode="hard",
            burn_in_ref=output.burn_in_ref,
            cover_ref=ready.packaging.cover_ref,
            expires_at=ready.content.expires_at,
            visibility="public",
            test_only=runtime.artifacts.describe(output.media_ref.artifact_id).test_only,
        )
    )
