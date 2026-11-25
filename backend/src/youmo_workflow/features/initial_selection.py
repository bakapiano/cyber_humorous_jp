from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import TaskDTO, VideoDTO
from youmo_workflow.domain.selection import (
    InitialOutput,
    InitialRequest,
    SelectionStagesDTO,
    SelectionVersion,
)
from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.features.tasks import add_task, task_dto
from youmo_workflow.storage.models import Batch, BatchItem, Task


def freeze_initial_task(
    db: Session, batch: Batch, items: list[VideoDTO], at: str, version: SelectionVersion = "v008"
) -> Task:
    task = add_task(db, "initial_selection", f"initial:{version}:{batch.id}", at)
    if task.input_json == "{}":
        task.batch_id = batch.id
        task.input_json = InitialRequest(
            batch_id=batch.id, snapshot_at=batch.snapshot_at, items=items
        ).model_dump_json()
        task.max_attempts = 1
    return task


def enqueue_initial(
    factory: sessionmaker[Session],
    batch_id: str,
    clock: Callable[[], datetime] = now_utc,
    version: SelectionVersion = "v008",
) -> TaskDTO:
    with factory.begin() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        batch = db.get(Batch, batch_id)
        if batch is None:
            raise WorkflowError("BATCH_NOT_FOUND", "批次不存在。")
        if batch.reservations_released:
            raise WorkflowError("BATCH_RETIRED", "该旧快照只读保留，素材已转入按数量触发的新批次。")
        existing = db.scalar(select(Task).where(Task.unique_key == f"initial:{version}:{batch_id}"))
        if existing:
            return task_dto(existing)
        active = db.scalar(
            select(Task).where(
                Task.batch_id == batch_id, Task.status.in_(["pending", "running", "retry_wait"])
            )
        )
        if active:
            raise WorkflowError(
                "BATCH_ALREADY_ACTIVE", "该批次还有任务在执行，未启动另一轮模型调用。"
            )
        if batch.status not in {"failed", "completed"}:
            raise WorkflowError("INITIAL_RUN_NOT_ALLOWED", "不能对已推进的批次重复启动初筛。")
        if db.scalar(
            select(Task.id).where(
                Task.batch_id == batch_id, Task.kind.in_(["packaging", "prepare_publication"])
            )
        ):
            raise WorkflowError(
                "INITIAL_RUN_NOT_ALLOWED", "该批次已推进到包装或发布准备，不能覆盖其选题版本。"
            )
        rows = db.scalars(
            select(BatchItem.snapshot_json)
            .where(BatchItem.batch_id == batch_id)
            .order_by(BatchItem.video_id)
        )
        items = [VideoDTO.model_validate_json(row) for row in rows]
        if not items:
            raise WorkflowError("INITIAL_INPUT_EMPTY", "批次没有候选，未调用模型。")
        task = freeze_initial_task(db, batch, items, stamp(clock()), version)
        batch.status, batch.error_code, batch.error_message = "queued", None, None
        return task_dto(task)


def selection_results(factory: sessionmaker[Session], batch_id: str) -> SelectionStagesDTO:
    with factory() as db:
        if db.get(Batch, batch_id) is None:
            raise WorkflowError("BATCH_NOT_FOUND", "批次不存在。")
        rows = list(
            db.scalars(
                select(Task).where(
                    Task.batch_id == batch_id,
                    Task.kind.in_(["initial_selection", "rescreen"]),
                )
            )
        )
        chosen = (
            "v008" if any(row.unique_key == f"initial:v008:{batch_id}" for row in rows) else "v004"
        )
        results, history = outputs_for_version(rows, chosen)
        return SelectionStagesDTO(
            initial=results.get("initial_selection"),
            rescreen=results.get("rescreen"),
            history=history,
        )


def outputs_for_version(
    rows: list[Task], version: str
) -> tuple[dict[str, InitialOutput], list[InitialOutput]]:
    results: dict[str, InitialOutput] = {}
    history: list[InitialOutput] = []
    for row in rows:
        if row.status != "succeeded" or not row.output_json:
            continue
        output = InitialOutput.model_validate_json(row.output_json)
        if output.version == version and f":{version}:" in row.unique_key:
            results[row.kind] = output
        elif row.kind == "initial_selection":
            history.append(output)
    return results, history
