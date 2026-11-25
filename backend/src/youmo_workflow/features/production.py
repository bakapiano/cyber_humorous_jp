from sqlalchemy import select

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import TaskDTO
from youmo_workflow.domain.publication import PublicationDTO, PublicationManifest
from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.features.sources import video_dto
from youmo_workflow.features.tasks import add_task, task_dto
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task, Video


def enqueue_video(runtime: Runtime, video_id: str) -> TaskDTO:
    with runtime.factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = db.get(Video, video_id)
        if row is None:
            raise WorkflowError("VIDEO_NOT_FOUND", "仅可制作已入库并核实来源的视频。")
        existing = db.scalar(select(Task).where(Task.unique_key == f"pilot:v001:{video_id}"))
        if existing:
            return task_dto(existing)
        task = add_task(db, "prepare_video", f"pilot:v001:{video_id}", stamp(now_utc()))
        task.input_json = video_dto(row).model_dump_json()
        task.max_attempts = 1
        return task_dto(task)


def confirm_reprint(runtime: Runtime, publication_id: str, expected_hash: str) -> PublicationDTO:
    with runtime.factory() as db:
        row = runtime.publications.require(db, publication_id)
        if row.manifest_hash != expected_hash:
            raise WorkflowError("CONFIRMATION_STALE", "待确认的发布包已改变。")
        manifest = PublicationManifest.model_validate_json(row.manifest_json)
    if manifest.test_only or manifest.visibility != "private":
        raise WorkflowError("PRIVATE_UPLOAD_NOT_ELIGIBLE", "不确认测试或公开稿件。")
    manifest.rights_confirmed = True
    manifest.rights_note = "用户确认本次以转载方式上传，附原始来源，仅自己可见；不声明为原创。"
    prepared = runtime.publications.prepare(manifest)
    return runtime.publications.confirm(prepared.id, prepared.manifest_hash)
