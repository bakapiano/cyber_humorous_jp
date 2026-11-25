from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import case, literal, select, true
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.media_limits import SourceTooLong
from youmo_workflow.domain.models import TaskDTO
from youmo_workflow.domain.source_skip import SourceDownloadFailed, SourceDurationEvidence
from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.features.batch_progress import refresh_batch_progress
from youmo_workflow.features.pipeline_gates import blocked_kinds, hold_dependency
from youmo_workflow.features.publication_cooldown import RATE_LIMIT_ERRORS, active_until
from youmo_workflow.features.remote_hold import remote_held
from youmo_workflow.features.review_skips import mark_review_skip
from youmo_workflow.features.source_skips import mark_source_skip
from youmo_workflow.features.task_recovery import prepare_retry, quarantine_upload
from youmo_workflow.storage.models import Task


def task_dto(row: Task) -> TaskDTO:
    return TaskDTO(
        id=row.id,
        kind=row.kind,
        status=row.status,
        batch_id=row.batch_id,
        attempts=row.attempts,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def add_task(db: Session, kind: str, unique_key: str, at: str) -> Task:
    old = db.scalar(select(Task).where(Task.unique_key == unique_key))
    if old:
        return old
    task = Task(
        id=uuid4().hex,
        unique_key=unique_key,
        kind=kind,
        created_at=at,
        updated_at=at,
        next_run_at=at,
    )
    db.add(task)
    db.flush()
    return task


class TaskService:
    def __init__(self, factory: sessionmaker[Session], clock: Callable[[], datetime] = now_utc):
        self.factory = factory
        self.clock = clock

    def list_tasks(self) -> list[TaskDTO]:
        with self.factory() as db:
            rows = db.scalars(select(Task).order_by(Task.created_at.desc()).limit(100))
            return [task_dto(row) for row in rows]

    def claim(
        self, kinds: tuple[str, ...] | None = None, *, downstream_first: bool = False
    ) -> Task | None:
        at = self.clock()
        priority = (
            case(
                {
                    "cleanup_batch": 0,
                    "cleanup_publication": 0,
                    "finalize_private": 0,
                    "publish": 1,
                    "prepare_publication": 2,
                    "editing": 3,
                    "packaging": 4,
                },
                value=Task.kind,
                else_=5,
            )
            if downstream_first
            else literal(0)
        )
        with self.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            upload_blocked = (kinds is None or "publish" in kinds) and active_until(db, at)
            remote_busy = remote_held(db) or db.scalar(
                select(Task.id)
                .where(Task.kind.in_(["publish", "finalize_private"]), Task.status == "running")
                .limit(1)
            )
            held_kinds = blocked_kinds(db)
            row = db.scalar(
                select(Task)
                .where(
                    Task.status.in_(["pending", "retry_wait"]),
                    Task.next_run_at <= stamp(at),
                    Task.kind.in_(kinds) if kinds is not None else true(),
                    Task.kind != "publish" if upload_blocked else true(),
                    Task.kind.not_in(["publish", "finalize_private"]) if remote_busy else true(),
                    Task.kind.not_in(held_kinds),
                )
                .order_by(priority, Task.created_at, Task.id)
                .limit(1)
            )
            if row is None:
                return None
            row.status = "running"
            row.attempts += 1
            row.lease_token = uuid4().hex
            row.lease_until = stamp(at + timedelta(minutes=lease_minutes(row)))
            row.updated_at = stamp(at)
            db.flush()
            db.expunge(row)
            return row

    def finish(self, task: Task, output: str = "{}") -> bool:
        with self.factory.begin() as db:
            row = db.get(Task, task.id)
            if not self.owns(row, task):
                return False
            assert row is not None
            row.status = "succeeded"
            row.output_json = output
            row.error_code = row.error_message = None
            row.updated_at = stamp(self.clock())
            row.lease_token = row.lease_until = None
            return True

    def fail(self, task: Task, error: WorkflowError) -> bool:
        with self.factory.begin() as db:
            row = db.get(Task, task.id)
            if not self.owns(row, task):
                return False
            assert row is not None
            if row.kind == "publish" and error.code in {
                "BILI_UPLOAD_COOLDOWN",
                "BILI_UPLOAD_BUSY",
                "REMOTE_ACCESS_BLOCKED",
            }:
                row.attempts = max(0, row.attempts - 1)
            rate_limited = row.kind == "publish" and error.code in RATE_LIMIT_ERRORS
            retry = error.retryable and (rate_limited or row.attempts < row.max_attempts)
            row.status = "retry_wait" if retry else "failed"
            if row.kind == "publish":
                uncertain = quarantine_upload(db, row)
                if uncertain or error.code == "SUBMIT_RESULT_UNKNOWN":
                    row.status = "unknown"
            delay = 60 if row.kind == "finalize_private" else 30 * 2 ** min(row.attempts, 10)
            row.next_run_at = stamp(self.clock() + timedelta(seconds=delay))
            if rate_limited:
                row.next_run_at = active_until(db, self.clock()) or stamp(
                    self.clock() + timedelta(minutes=30)
                )
            row.error_code, row.error_message = error.code, error.message
            row.updated_at = stamp(self.clock())
            row.lease_token = row.lease_until = None
            mark_review_skip(db, row, self.clock())
            if isinstance(error, SourceTooLong):
                mark_source_skip(
                    db,
                    row,
                    SourceDurationEvidence(
                        video_id=error.video_id, duration_seconds=error.duration
                    ),
                    self.clock(),
                )
            elif isinstance(error, SourceDownloadFailed):
                mark_source_skip(db, row, error.evidence, self.clock())
            hold_dependency(db, row, error, self.clock())
            return True

    def owns(self, row: Task | None, task: Task) -> bool:
        return bool(
            row
            and row.status == "running"
            and row.lease_token == task.lease_token
            and row.lease_until
            and row.lease_until > stamp(self.clock())
        )

    def recover(self) -> int:
        with self.factory.begin() as db:
            rows = list(
                db.scalars(
                    select(Task).where(
                        Task.status == "running",
                        Task.lease_until <= stamp(self.clock()),
                    )
                )
            )
            for row in rows:
                row.status = recovery_status(row)
                if row.kind == "publish":
                    quarantine_upload(db, row)
                row.lease_token = row.lease_until = None
                row.updated_at = stamp(self.clock())
                row.error_code = "WORKER_INTERRUPTED"
                row.error_message = "执行租约过期，已按操作类型恢复；外部写入不会盲目重发。"
            batches = {row.batch_id for row in rows}
        for batch_id in batches:
            refresh_batch_progress(self.factory, batch_id, self.clock())
        return len(rows)

    def retry(self, task_id: str) -> TaskDTO:
        with self.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = db.get(Task, task_id)
            if row is None:
                raise WorkflowError("TASK_NOT_FOUND", "任务不存在。")
            if row.kind == "publish" or row.status != "failed":
                raise WorkflowError("RETRY_NOT_ALLOWED", "只允许重试已失败的非投稿任务。")
            prepare_retry(db, row, self.clock())
            if retired_selection(db, row):
                raise WorkflowError(
                    "RETRY_NOT_ALLOWED", "该批次已启动 v008，旧选题阶段只保留记录，请重试新版任务。"
                )
            row.next_run_at = row.updated_at = stamp(self.clock())
            return task_dto(row)


def recovery_status(row: Task) -> str:
    if row.kind in {
        "initial_selection",
        "prepare_content",
        "prepare_video",
        "packaging",
        "editing",
    }:
        return "failed"
    if row.kind == "publish":
        return "unknown"
    return "failed" if row.attempts >= row.max_attempts else "pending"


def lease_minutes(row: Task) -> int:
    if row.kind == "packaging":
        return 720
    if row.kind in {"editing", "prepare_content", "prepare_video"}:
        return 240
    if row.kind == "publish":
        return 120
    if row.kind != "initial_selection":
        return 15
    return 480 if row.unique_key.startswith("initial:v008:") else 240


def retired_selection(db: Session, row: Task) -> bool:
    old = row.kind == "selection" or ":v004:" in row.unique_key
    return bool(
        old and db.scalar(select(Task.id).where(Task.unique_key == f"initial:v008:{row.batch_id}"))
    )
