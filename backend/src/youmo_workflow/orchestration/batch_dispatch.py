from collections.abc import Callable
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.batch_plan import BatchExecution, ProductionDispatch
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.selection import InitialOutput
from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.features.tasks import TaskService, add_task
from youmo_workflow.storage.models import Batch, Task


class BatchDispatcher:
    def __init__(
        self, factory: sessionmaker[Session], clock: Callable[[], datetime] = now_utc
    ) -> None:
        self.factory, self.clock = factory, clock

    def run(self, task: Task) -> str:
        result = InitialOutput.model_validate_json(task.input_json)
        with self.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            owned = db.get(Task, task.id)
            if not TaskService(self.factory, self.clock).owns(owned, task):
                raise WorkflowError("LEASE_EXPIRED", "制作分发租约已失效。")
            batch = db.get(Batch, result.batch_id)
            if batch is None or not batch.execution_json or batch.id != task.batch_id:
                raise WorkflowError("BATCH_AUTHORIZATION_REQUIRED", "缺少批次执行授权。")
            execution = BatchExecution.model_validate_json(batch.execution_json)
            ids = result.shortlisted_ids[: execution.publications_per_batch]
            if len(ids) != execution.publications_per_batch:
                raise WorkflowError(
                    "SELECTION_TOO_SMALL", "选题结果不足设置的投稿数，未擅自补齐或缩减。"
                )
            videos = {row.video.video_id: row.video for row in result.ranked}
            output = ProductionDispatch(
                batch_id=batch.id,
                video_ids=ids,
                publications_per_batch=execution.publications_per_batch,
            ).model_dump_json()
            for video_id in ids:
                child = add_task(
                    db, "prepare_video", f"batch-video:{batch.id}:{video_id}", stamp(self.clock())
                )
                child.batch_id, child.input_json = batch.id, videos[video_id].model_dump_json()
                child.max_attempts = 1
            assert owned is not None
            owned.status, owned.output_json = "succeeded", output
            owned.lease_token = owned.lease_until = None
            return output
