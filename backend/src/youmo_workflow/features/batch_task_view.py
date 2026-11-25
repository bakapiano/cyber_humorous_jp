from sqlalchemy import select
from sqlalchemy.orm import Session

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import SelectedContent
from youmo_workflow.domain.replacements import Replacement
from youmo_workflow.storage.models import Publication, Task, WorkflowSetting

REPLACEMENT_PREFIX = "publication_replacement:"


def effective_batch_tasks(db: Session, batch_id: str) -> list[Task]:
    rows = list(db.scalars(select(Task).where(Task.batch_id == batch_id)))
    allowed: set[str] = set()
    for setting in db.scalars(
        select(WorkflowSetting).where(WorkflowSetting.id.startswith(REPLACEMENT_PREFIX))
    ):
        record = Replacement.model_validate_json(setting.value_json)
        if record.batch_id != batch_id:
            continue
        old, replacement = (
            db.get(Publication, record.old_publication_id),
            db.get(Task, record.packaging_task_id),
        )
        if (
            old is None
            or old.status != "withdrawn"
            or old.remote_id != record.old_remote_id
            or old.manifest_hash != record.old_manifest_hash
            or replacement is None
            or replacement.batch_id != batch_id
            or replacement.kind != "packaging"
            or SelectedContent.model_validate_json(replacement.input_json).content_id
            != record.new_content_id
        ):
            raise WorkflowError(
                "REPLACEMENT_UNVERIFIED", "替代稿链路不完整，禁止跳过旧任务或清理视频。"
            )
        allowed.update(record.superseded_task_ids)
    if any(row.status == "superseded" and row.id not in allowed for row in rows):
        raise WorkflowError("REPLACEMENT_UNVERIFIED", "存在没有重做依据的被替代任务。")
    return [row for row in rows if row.status != "superseded"]
