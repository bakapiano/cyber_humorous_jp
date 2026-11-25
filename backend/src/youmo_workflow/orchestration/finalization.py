import json
from dataclasses import replace

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.remote import PublicationGateway
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.delivery_cleanup import enqueue_delivery_cleanup
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Publication, Task


class PrivateFinalizer:
    """Hard subtitles finish from the local upload receipt; soft behavior is legacy-only."""

    def __init__(
        self,
        runtime: Runtime,
        gateway: PublicationGateway,
        *,
        legacy_native_subtitles: bool = False,
    ) -> None:
        self.runtime, self.gateway = runtime, gateway
        self.legacy_native_subtitles = legacy_native_subtitles

    def run(self, task: Task) -> str:
        request = FinalizePrivate.model_validate_json(task.input_json)
        with self.runtime.factory.begin() as db:
            row = db.get(Task, task.id)
            if not self.runtime.tasks.owns(row, task):
                raise WorkflowError("LEASE_EXPIRED", "字幕收尾任务租约失效。")
            publication = self.runtime.publications.require(db, request.publication_id)
            if (
                publication.manifest_hash != request.manifest_hash
                or publication.confirmed_hash != request.manifest_hash
            ):
                raise WorkflowError("CONFIRMATION_STALE", "字幕任务与已确认发布包不匹配。")
            manifest = PublicationManifest.model_validate_json(publication.manifest_json)
            if manifest.test_only or not manifest.rights_confirmed:
                raise WorkflowError(
                    "PRIVATE_UPLOAD_NOT_ELIGIBLE", "字幕任务仅适用于已授权的真实转载。"
                )
            if manifest.subtitle_mode == "hard":
                result = complete_delivery(publication)
                enqueue_delivery_cleanup(db, publication, task, stamp(self.runtime.tasks.clock()))
                return result
        scoped = replace(
            self.runtime, settings=replace(self.runtime.settings, publication_enabled=True)
        )
        executor = PublicationExecutor(
            scoped, self.gateway, legacy_native_subtitles=self.legacy_native_subtitles
        )
        status = executor.reconcile(request.publication_id)
        if status in {"subtitle_rejected", "withdrawn"}:
            raise WorkflowError(
                "SUBTITLE_REVISION_REQUIRED", "字幕已退回或稿件已下架，不重复提交同一版本。"
            )
        if status == "awaiting_subtitles":
            try:
                status = executor.attach_subtitles(request.publication_id)
            except WorkflowError as error:
                if error.code != "SUBTITLE_RESULT_UNKNOWN":
                    raise
                status = "subtitle_unknown"
        if status != "completed":
            raise WorkflowError(
                "PRIVATE_FINALIZE_PENDING",
                "等待审核或字幕传播；只读回查，不重复写入。",
                retryable=True,
            )
        return json.dumps(
            {
                "status": status,
                "subtitle_uploaded": manifest.subtitle_mode == "soft",
                "subtitle_mode": manifest.subtitle_mode,
            }
        )


def complete_delivery(publication: Publication) -> str:
    """Acknowledge delivery, without claiming review approval or doing network I/O."""
    if not publication.remote_id or publication.status not in {
        "uploaded",
        "reviewing",
        "completed",
    }:
        raise WorkflowError("DELIVERY_NOT_CONFIRMED", "缺少明确投稿回执，保留视频。")
    previous = publication.status
    if previous == "reviewing":
        publication.status = "uploaded"
    return json.dumps(
        {
            "status": "delivered",
            "completion_basis": "upload_receipt",
            "remote_id": publication.remote_id,
            "subtitle_mode": "hard",
            "subtitle_uploaded": False,
            "remote_review_checked": False,
            "previous_publication_status": previous,
        }
    )
