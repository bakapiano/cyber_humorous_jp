import json
from dataclasses import replace

from sqlalchemy import select

from youmo_workflow.domain.batch_plan import BatchExecution, ProductionDispatch
from youmo_workflow.domain.editing import EditedContent
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.publication import PublicationDTO, PublicationManifest
from youmo_workflow.domain.remote import PublicationGateway
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_visibility import visibility
from youmo_workflow.features.publications import manifest_hash
from youmo_workflow.features.tasks import add_task
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task, WorkflowSetting


class BatchPublisher:
    """A frozen, explicitly authorized batch can submit only its selected outputs."""

    def __init__(self, runtime: Runtime, gateway: PublicationGateway) -> None:
        self.runtime, self.gateway = runtime, gateway

    def authorize(self, task: Task, prepared: PublicationDTO) -> None:
        with self.runtime.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            batch = db.get(Batch, task.batch_id) if task.batch_id else None
            if batch is None or batch.execution_json is None:
                return
            execution = BatchExecution.model_validate_json(batch.execution_json)
            if not self.runtime.tasks.owns(db.get(Task, task.id), task):
                raise WorkflowError("LEASE_EXPIRED", "发布包授权租约失效。")
            dispatch = db.scalar(
                select(Task).where(
                    Task.batch_id == batch.id,
                    Task.kind == "dispatch_production",
                    Task.status == "succeeded",
                )
            )
            if dispatch is None or not dispatch.output_json:
                raise WorkflowError("BATCH_AUTHORIZATION_REQUIRED", "缺少已完成的制作分发记录。")
            output = ProductionDispatch.model_validate_json(dispatch.output_json)
            video_id = EditedContent.model_validate_json(task.input_json).content.video_id
            if (
                video_id not in output.video_ids
                or len(output.video_ids) != execution.publications_per_batch
            ):
                raise WorkflowError("PUBLICATION_OUTSIDE_BATCH", "发布包不在本批授权的投稿清单中。")
            row = self.runtime.publications.require(db, prepared.id)
            existing = db.scalar(
                select(Task).where(Task.unique_key == f"batch-publish:{prepared.id}")
            )
            if existing:
                return
            if row.manifest_hash != prepared.manifest_hash or row.status not in {
                "prepared",
                "confirmed",
            }:
                raise WorkflowError("CONFIRMATION_STALE", "发布包改变或已经提交。")
            manifest = PublicationManifest.model_validate_json(row.manifest_json)
            if manifest.test_only:
                raise WorkflowError(
                    "PRIVATE_UPLOAD_NOT_ELIGIBLE", "批次仅授权真实转载，不授权测试稿件。"
                )
            manifest.rights_confirmed = True
            manifest.visibility = visibility(db, batch)
            label = "公开可见" if manifest.visibility == "public" else "仅自己可见"
            manifest.rights_note = (
                f"用户授权批次 {batch.id}：转载、附原始来源、{label}；不声明原创。"
            )
            row.manifest_json = manifest.model_dump_json()
            row.manifest_hash = row.confirmed_hash = manifest_hash(manifest)
            row.status = "queued"
            child = add_task(
                db, "publish", f"batch-publish:{prepared.id}", stamp(self.runtime.tasks.clock())
            )
            child.batch_id, child.max_attempts = batch.id, 3
            child.input_json = FinalizePrivate(
                publication_id=row.id, manifest_hash=row.manifest_hash
            ).model_dump_json()
            owned = db.get(Task, task.id)
            assert owned is not None
            owned.status, owned.output_json = "succeeded", prepared.model_dump_json()
            owned.lease_token = owned.lease_until = None

    def run(self, task: Task) -> str:
        request = FinalizePrivate.model_validate_json(task.input_json)
        with self.runtime.factory() as db:
            batch = db.get(Batch, task.batch_id) if task.batch_id else None
            if batch is None or not batch.execution_json or task.kind != "publish":
                raise WorkflowError("BATCH_AUTHORIZATION_REQUIRED", "投稿任务没有冻结的批次授权。")
            execution = BatchExecution.model_validate_json(batch.execution_json)
            durable = (
                execution.producer_consumer
                or db.get(WorkflowSetting, f"durable-delivery:{batch.id}") is not None
            )
            if not self.runtime.tasks.owns(db.get(Task, task.id), task):
                raise WorkflowError("LEASE_EXPIRED", "投稿任务租约失效。")
            publication = self.runtime.publications.require(db, request.publication_id)
            manifest = PublicationManifest.model_validate_json(publication.manifest_json)
            if manifest.visibility != visibility(db, batch):
                raise WorkflowError("CONFIRMATION_STALE", "投稿可见性与冻结批次授权不符。")
            if (
                publication.manifest_hash != request.manifest_hash
                or publication.confirmed_hash != request.manifest_hash
            ):
                raise WorkflowError("CONFIRMATION_STALE", "投稿任务与已确认发布包不匹配。")
        scoped = replace(
            self.runtime, settings=replace(self.runtime.settings, publication_enabled=True)
        )
        remote_id = PublicationExecutor(scoped, self.gateway).submit(
            request.publication_id, task, allow_expired=durable
        )
        return json.dumps({"remote_id": remote_id})
