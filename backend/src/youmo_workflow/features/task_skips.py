from sqlalchemy.orm import Session

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.features.review_skips import verified_review_skips
from youmo_workflow.features.source_skips import verified_source_skips
from youmo_workflow.storage.models import Task


def verified_skips(db: Session, batch_id: str, tasks: list[Task]) -> set[str]:
    source_tasks = [
        task for task in tasks if task.status == "skipped" and task.kind == "prepare_video"
    ]
    source_ids = {task.id for task in source_tasks}
    sources = verified_source_skips(db, batch_id, source_tasks)
    reviews = verified_review_skips(
        db, batch_id, [task for task in tasks if task.id not in source_ids]
    )
    if sources & reviews:
        raise WorkflowError("TASK_SKIP_DUPLICATE", "同一素材存在重复跳过记录。")
    return sources | reviews
