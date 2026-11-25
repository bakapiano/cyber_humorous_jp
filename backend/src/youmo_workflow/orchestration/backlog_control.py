"""Bounded persisted consumer of explicitly authorized frozen backlog manifests."""

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.production_buffer import active_producer, producer_failed
from youmo_workflow.orchestration.backlog import BacklogCursor, BacklogDrain
from youmo_workflow.orchestration.backlog_prerequisites import require_repaired_predecessors
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, WorkflowSetting

SETTING = "frozen_backlog_run"


class FrozenSource(Contract):
    relative_path: str
    sha256: str


class BacklogRun(Contract):
    sources: list[FrozenSource] = Field(min_length=1, max_length=10)
    enabled: bool = True
    status: str = "running"
    authorized_at: str
    authority: str
    skip_review_rejections: bool = True
    last_error: str | None = None
    preexisting_batch_ids: list[str] = Field(default_factory=list[str])
    continued_after_failures: dict[str, str] = Field(default_factory=dict[str, str])


class BacklogController:
    def __init__(self, runtime: Runtime):
        self.runtime = runtime

    def start(self, paths: list[Path], confirm: Literal[True]) -> BacklogRun:
        if confirm is not True:
            raise WorkflowError("EXECUTION_CONFIRMATION_REQUIRED", "冻结积压执行缺少明确授权。")
        sources: list[FrozenSource] = []
        preexisting: list[str] = []
        for path in paths:
            drain = BacklogDrain(self.runtime, path)
            with self.runtime.factory() as db:
                previous = db.get(WorkflowSetting, drain.key)
                if previous:
                    preexisting.extend(
                        BacklogCursor.model_validate_json(previous.value_json).batch_ids
                    )
            sources.append(
                FrozenSource(
                    relative_path=path.resolve()
                    .relative_to(self.runtime.settings.root.resolve())
                    .as_posix(),
                    sha256=drain.sha256,
                )
            )
        requested = BacklogRun(
            sources=sources,
            authorized_at=stamp(self.runtime.tasks.clock()),
            authority="用户2026-11-07要求处理全部积压，审核拒绝明确跳过，不跳过未知写入或其他失败。",
            preexisting_batch_ids=preexisting,
        )
        with self.runtime.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            saved = db.get(WorkflowSetting, SETTING)
            if saved:
                prior = BacklogRun.model_validate_json(saved.value_json)
                if prior.sources != requested.sources:
                    raise WorkflowError(
                        "BACKLOG_RUN_CHANGED", "已有不同冻结范围，不覆盖原运行记录。"
                    )
                return prior
            db.add(WorkflowSetting(id=SETTING, revision=1, value_json=requested.model_dump_json()))
        return requested

    def resume_after_diagnosis(
        self, diagnoses: dict[str, str], confirm: Literal[True]
    ) -> BacklogRun:
        """Continue untouched slices without retrying or completing diagnosed failures."""
        if confirm is not True:
            raise WorkflowError("EXECUTION_CONFIRMATION_REQUIRED", "接续积压缺少明确授权。")
        with self.runtime.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            saved = db.get(WorkflowSetting, SETTING)
            if saved is None:
                raise WorkflowError("BACKLOG_RUN_MISSING", "冻结积压运行记录缺失。")
            run = BacklogRun.model_validate_json(saved.value_json)
            if run.status == "cancelled":
                raise WorkflowError("BACKLOG_RUN_CANCELLED", "用户已清空旧积压，不允许自动恢复。")
            require_repaired_predecessors(db)
            prefixes = tuple(f"backlog:{source.sha256}:" for source in run.sources)
            for identifier, reason in diagnoses.items():
                batch = db.get(Batch, identifier)
                if (
                    batch is None
                    or batch.status != "failed"
                    or not (batch.run_key or "").startswith(prefixes)
                    or not reason.strip()
                ):
                    raise WorkflowError("BACKLOG_DIAGNOSIS_INVALID", "诊断必须对应本轮失败批次。")
            run.continued_after_failures.update(diagnoses)
            run.enabled, run.status, run.last_error = True, "running", None
            saved.value_json, saved.revision = run.model_dump_json(), saved.revision + 1
            return run

    def tick(self) -> bool:
        with self.runtime.factory() as db:
            saved = db.get(WorkflowSetting, SETTING)
            if saved is None:
                return False
            run = BacklogRun.model_validate_json(saved.value_json)
            if not run.enabled:
                return run.status == "attention_required"
            if active_producer(db):
                return True
        try:
            self.advance(run)
        except WorkflowError as error:
            if error.code == "PRODUCTION_BUFFER_FULL":
                return True
            self.record_status(run, "attention_required", error.code)
        except (OSError, ValidationError):
            self.record_status(run, "attention_required", "BACKLOG_STATE_INVALID")
        return True

    def advance(self, run: BacklogRun) -> None:
        with self.runtime.factory() as db:
            require_repaired_predecessors(db)
        for source in run.sources:
            drain = BacklogDrain(
                self.runtime,
                self.runtime.settings.root / source.relative_path,
                skip_review_rejections=run.skip_review_rejections,
            )
            if drain.sha256 != source.sha256:
                raise WorkflowError("BACKLOG_CHANGED", "冻结积压范围发生改变，未创建新批次。")
            with self.runtime.factory() as db:
                saved = db.get(WorkflowSetting, drain.key)
                cursor = BacklogCursor.model_validate_json(saved.value_json) if saved else None
                enqueued = len(cursor.batch_ids) if cursor else 0
                for identifier in cursor.batch_ids if cursor else []:
                    batch = db.get(Batch, identifier)
                    if identifier not in (
                        run.preexisting_batch_ids + list(run.continued_after_failures)
                    ) and (batch is None or producer_failed(db, batch)):
                        raise WorkflowError(
                            "BACKLOG_BATCH_FAILED",
                            "本轮有非审核跳过的失败批次，停止追加并先诊断；不算作完成。",
                        )
            if enqueued < len(drain.snapshot.items) // drain.snapshot.plan.batch_size:
                drain.enqueue_next(True)
                return
        self.record_status(run, "no_full_slices_remaining", None)

    def record_status(self, run: BacklogRun, status: str, error: str | None) -> None:
        with self.runtime.factory.begin() as db:
            row = db.get(WorkflowSetting, SETTING)
            if row is None:
                raise WorkflowError("BACKLOG_RUN_MISSING", "运行记录缺失，停止自动接续。")
            current = BacklogRun.model_validate_json(row.value_json)
            if current.sources != run.sources:
                raise WorkflowError("BACKLOG_RUN_CHANGED", "运行范围已改变，不覆盖当前记录。")
            current.enabled, current.status, current.last_error = False, status, error
            row.value_json, row.revision = current.model_dump_json(), row.revision + 1
