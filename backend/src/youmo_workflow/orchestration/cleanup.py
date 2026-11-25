import re
from pathlib import Path

from sqlalchemy import update

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.cleanup import CleanupFile, CleanupPlan, CleanupResult
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.providers import SelectedContent
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.artifacts import file_hash
from youmo_workflow.features.batch_task_view import effective_batch_tasks
from youmo_workflow.features.delivery_cleanup import delivery_contents
from youmo_workflow.features.task_skips import verified_skips
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Artifact, Batch, Task


class BatchMediaCleanup:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    def run(self, task: Task) -> str:
        plan = CleanupPlan.model_validate_json(task.input_json)
        contents = self.verified_contents(task)
        if not plan.planned:
            plan = self.plan(plan.batch_id, contents).model_copy(
                update={"publication_id": plan.publication_id}
            )
            with self.runtime.factory.begin() as db:
                row = db.get(Task, task.id)
                if not self.runtime.tasks.owns(row, task):
                    raise WorkflowError("LEASE_EXPIRED", "视频清理租约失效。")
                assert row is not None
                row.input_json = plan.model_dump_json()
        for item in plan.files:
            parts = Path(item.relative_path).parts
            if len(parts) < 3 or parts[0] != "production" or parts[1] not in contents:
                raise WorkflowError("CLEANUP_PATH_DENIED", "清理文件不属于本批制作内容。")
            self.remove_file(task, item)
        return CleanupResult(
            batch_id=plan.batch_id,
            video_files=len(plan.files),
            released_bytes=sum(item.size_bytes for item in plan.files),
        ).model_dump_json()

    def verified_contents(self, task: Task) -> list[str]:
        with self.runtime.factory() as db:
            if not self.runtime.tasks.owns(db.get(Task, task.id), task):
                raise WorkflowError("LEASE_EXPIRED", "视频清理租约失效。")
            plan = CleanupPlan.model_validate_json(task.input_json)
            if task.kind == "cleanup_publication" and plan.publication_id:
                return delivery_contents(db, task, plan)
            if plan.publication_id or task.kind != "cleanup_batch":
                raise WorkflowError("CLEANUP_NOT_ELIGIBLE", "清理类型与记录不符。")
            batch = db.get(Batch, task.batch_id) if task.batch_id else None
            if batch is None or not batch.execution_json:
                raise WorkflowError("CLEANUP_NOT_ELIGIBLE", "没有已授权的整批生产记录。")
            execution = BatchExecution.model_validate_json(batch.execution_json)
            rows = [row for row in effective_batch_tasks(db, batch.id) if row.id != task.id]
            skipped = verified_skips(db, batch.id, rows)
            finalizers = [
                row for row in rows if row.kind == "finalize_private" and row.status == "succeeded"
            ]
            if len(finalizers) + len(skipped) != execution.publications_per_batch or any(
                row.status not in {"succeeded", "skipped"} for row in rows
            ):
                raise WorkflowError("CLEANUP_NOT_ELIGIBLE", "整批投递收尾尚未全部完成，保留视频。")
            published_contents: set[str] = set()
            for row in finalizers:
                request = FinalizePrivate.model_validate_json(row.input_json)
                publication = self.runtime.publications.require(db, request.publication_id)
                manifest = PublicationManifest.model_validate_json(publication.manifest_json)
                delivered = publication.status == "completed" or (
                    manifest.subtitle_mode == "hard" and publication.status == "uploaded"
                )
                if (
                    not delivered
                    or not publication.remote_id
                    or publication.manifest_hash != request.manifest_hash
                    or manifest.source_url
                    in {f"https://www.youtube.com/watch?v={video_id}" for video_id in skipped}
                ):
                    raise WorkflowError(
                        "CLEANUP_NOT_ELIGIBLE", "稿件未确认投递或发布包已改变，保留视频。"
                    )
                published_contents.add(publication.content_id)
            return sorted(
                published_contents
                | {
                    SelectedContent.model_validate_json(row.output_json or "{}").content_id
                    for row in rows
                    if row.kind == "prepare_video"
                    and row.status == "succeeded"
                    and SelectedContent.model_validate_json(row.output_json or "{}").video_id
                    not in skipped
                }
            )

    def plan(self, batch_id: str, contents: list[str]) -> CleanupPlan:
        root = self.runtime.artifacts.root
        files: list[CleanupFile] = []
        for content_id in contents:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", content_id):
                raise WorkflowError("CLEANUP_PATH_DENIED", "内容标识不安全，拒绝清理。")
            directory = root / "production" / content_id
            if not directory.exists():
                continue
            checked_path(root, directory)
            for path in directory.rglob("*"):
                if path.is_file() and is_video(path):
                    checked_path(root, path)
                    files.append(
                        CleanupFile(
                            relative_path=path.relative_to(root).as_posix(),
                            sha256=file_hash(path),
                            size_bytes=path.stat().st_size,
                        )
                    )
        return CleanupPlan(batch_id=batch_id, files=files, planned=True)

    def remove_file(self, task: Task, item: CleanupFile) -> None:
        root = self.runtime.artifacts.root
        path = checked_path(root, root / item.relative_path)
        if not is_video(path):
            raise WorkflowError("CLEANUP_PATH_DENIED", "清理仅允许本工作流视频文件。")
        with self.runtime.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if not self.runtime.tasks.owns(db.get(Task, task.id), task):
                raise WorkflowError("LEASE_EXPIRED", "视频清理租约失效。")
            if path.exists():
                if path.stat().st_size != item.size_bytes or file_hash(path) != item.sha256:
                    raise WorkflowError("CLEANUP_FILE_CHANGED", "待清理视频发生变化，拒绝删除。")
                path.unlink()
            db.execute(
                update(Artifact)
                .where(Artifact.relative_path == item.relative_path)
                .values(cleaned_at=stamp(self.runtime.tasks.clock()))
            )


def is_video(path: Path) -> bool:
    candidate = path.with_suffix("") if path.suffix == ".part" else path
    return candidate.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".m4a", ".ts"}


def checked_path(root: Path, path: Path) -> Path:
    allowed = root / "production"
    resolved = path.resolve()
    if (
        not path.is_relative_to(root)
        or not resolved.is_relative_to(allowed.resolve())
        or resolved == allowed.resolve()
    ):
        raise WorkflowError("CLEANUP_PATH_DENIED", "清理路径越界。")
    current = path
    while current != root:
        if current.is_symlink() or current.is_junction():
            raise WorkflowError("CLEANUP_PATH_DENIED", "清理路径包含链接，拒绝删除。")
        current = current.parent
    return resolved
