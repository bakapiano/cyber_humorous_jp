from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import SelectionOutput
from youmo_workflow.domain.selection import (
    ContentPreparationProvider,
    InitialOutput,
    InitialProvider,
    InitialRequest,
    RescreenProvider,
)
from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.features.batches import BatchService
from youmo_workflow.features.tasks import TaskService, add_task
from youmo_workflow.storage.models import Batch, Task, Video


@dataclass
class SelectionProviders:
    initial: InitialProvider
    rescreen: RescreenProvider
    content: ContentPreparationProvider
    v008: InitialProvider | None = None


class SelectionStages:
    def __init__(
        self,
        factory: sessionmaker[Session],
        providers: SelectionProviders,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.factory, self.providers, self.clock = factory, providers, clock
        self.tasks, self.batches = TaskService(factory, clock), BatchService(factory, clock)

    def run(self, task: Task) -> str:
        if task.kind == "initial_selection":
            return self.initial(task)
        if task.kind == "rescreen":
            return self.rescreen(task)
        return self.content(task)

    def initial(self, task: Task) -> str:
        request = InitialRequest.model_validate_json(task.input_json)
        if request.batch_id != task.batch_id:
            raise WorkflowError("INITIAL_INPUT_INVALID", "任务与初筛批次不一致。")
        self.batches.set_result(request.batch_id, "running")
        upgraded = task.unique_key.startswith("initial:v008:")
        provider = self.providers.v008 if upgraded else self.providers.initial
        if provider is None:
            raise WorkflowError(
                "INITIAL_SELECTION_NOT_CONFIGURED", "v008 初筛未注册，未回退到旧版代替执行。"
            )
        result = provider.rank(request)
        if result.version != ("v008" if upgraded else "v004"):
            raise WorkflowError("SELECTION_VERSION_CHANGED", "任务与返回的初筛版本不一致。")
        validate_initial(request, result)
        payload = result.model_dump_json()
        self.advance(task, payload, "rescreen" if result.shortlisted_ids else None)
        if not result.shortlisted_ids:
            self.batches.set_result(request.batch_id, "completed")
        return payload

    def rescreen(self, task: Task) -> str:
        request = InitialOutput.model_validate_json(task.input_json)
        original = request.model_dump_json()
        result = self.providers.rescreen.review(request)
        if result.model_dump_json() != original:
            raise WorkflowError(
                "RESCREEN_NOT_PASSTHROUGH", "当前复筛必须原样输出，不允许删减、重排或改写。"
            )
        with self.factory() as db:
            batch = db.get(Batch, request.batch_id)
            next_kind = (
                "dispatch_production" if batch and batch.execution_json else "prepare_content"
            )
        self.advance(task, task.input_json, next_kind)
        return task.input_json

    def content(self, task: Task) -> str:
        request = InitialOutput.model_validate_json(task.input_json)
        self.batches.set_result(request.batch_id, "running")
        result = self.providers.content.prepare(request)
        validate_preparation(request, result)
        payload = result.model_dump_json()
        with self.factory.begin() as db:
            row = self.owned(db, task)
            for content in result.selected:
                child = add_task(
                    db, "packaging", f"packaging:{content.content_id}", stamp(self.clock())
                )
                child.batch_id, child.input_json = result.batch_id, content.model_dump_json()
            self.finish(row, payload)
        return payload

    def advance(self, task: Task, payload: str, next_kind: str | None) -> None:
        with self.factory.begin() as db:
            row = self.owned(db, task)
            if task.kind == "initial_selection":
                request = InitialRequest.model_validate_json(task.input_json)
                db.execute(
                    update(Video)
                    .where(Video.id.in_([item.video_id for item in request.items]))
                    .values(screened=True)
                )
            if next_kind:
                version = InitialOutput.model_validate_json(payload).version
                child = add_task(
                    db, next_kind, f"{next_kind}:{version}:{task.batch_id}", stamp(self.clock())
                )
                child.batch_id, child.input_json = task.batch_id, payload
            self.finish(row, payload)

    def owned(self, db: Session, task: Task) -> Task:
        row = db.get(Task, task.id)
        if row is None or not self.tasks.owns(row, task):
            raise WorkflowError("LEASE_EXPIRED", "任务租约已失效，不提交旧结果或重复创建下游任务。")
        return row

    def finish(self, row: Task, payload: str) -> None:
        row.output_json, row.status = payload, "succeeded"
        row.error_code = row.error_message = None
        row.lease_token = row.lease_until = None
        row.updated_at = stamp(self.clock())


def validate_initial(request: InitialRequest, result: InitialOutput) -> None:
    ids = {row.video_id for row in request.items}
    ranked = [row.video.video_id for row in result.ranked]
    selected = result.shortlisted_ids
    returned = selected + result.not_shortlisted_ids
    invalid = (
        result.batch_id != request.batch_id
        or result.shortlist_limit != 30
        or result.rank_count != min(50, len(ids))
        or len(ranked) != result.rank_count
        or len(set(ranked)) != len(ranked)
        or not set(ranked) <= ids
        or selected != ranked[:30]
        or len(returned) != len(set(returned))
        or set(returned) != ids
    )
    if invalid:
        raise WorkflowError(
            "INITIAL_SELECTION_INVALID", "初筛输出的来源、排序、Top50/Top30 数量不符合契约。"
        )
    originals = {row.video_id: row for row in request.items}
    if any(row.video != originals[row.video.video_id] for row in result.ranked):
        raise WorkflowError("INITIAL_SELECTION_INVALID", "初筛输出修改了冻结的来源元数据。")


def validate_preparation(request: InitialOutput, result: SelectionOutput) -> None:
    ids = [row.video_id for row in result.selected]
    if result.batch_id != request.batch_id or ids != request.shortlisted_ids or result.rejected_ids:
        raise WorkflowError(
            "CONTENT_PREPARATION_INVALID", "内容准备不能代替复筛删减或重排入围候选。"
        )
    originals = {row.video.video_id: row.video.title for row in request.ranked}
    if len({row.content_id for row in result.selected}) != len(ids) or any(
        row.original_title != originals[row.video_id] for row in result.selected
    ):
        raise WorkflowError(
            "CONTENT_PREPARATION_INVALID", "内容准备不能重复内容身份或修改原始标题。"
        )
