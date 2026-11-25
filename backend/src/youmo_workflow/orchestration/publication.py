from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.remote import (
    PublicationGateway,
    RemoteSubmission,
    SubtitleRevisionGateway,
    UploadFiles,
)
from youmo_workflow.domain.time import now_utc, parse_stamp, stamp
from youmo_workflow.features.pipeline_gates import hold_dependency
from youmo_workflow.features.publication_cooldown import (
    RATE_LIMIT_ERRORS,
    clear_rejections,
    pace_attempt,
    record_rejection,
    require_upload_slot,
)
from youmo_workflow.features.publication_recovery import resume_recovered_upload
from youmo_workflow.features.subtitle_revisions import active_revision
from youmo_workflow.features.tasks import add_task
from youmo_workflow.features.video_review import REJECTED_LABELS, record_video_rejection
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Publication, Task


class PublicationExecutor:
    def __init__(
        self,
        runtime: Runtime,
        gateway: PublicationGateway,
        *,
        legacy_native_subtitles: bool = False,
    ) -> None:
        self.runtime, self.gateway = runtime, gateway
        self.legacy_native_subtitles = legacy_native_subtitles

    def submit_private(self, publication_id: str, task: Task | None = None) -> str:
        with self.runtime.factory() as db:
            row = self.runtime.publications.require(db, publication_id)
            if PublicationManifest.model_validate_json(row.manifest_json).visibility != "private":
                raise WorkflowError("PRIVATE_UPLOAD_NOT_ELIGIBLE", "私密投稿接口不能发布公开稿件。")
        return self.submit(publication_id, task)

    def submit(
        self, publication_id: str, task: Task | None = None, *, allow_expired: bool = False
    ) -> str:
        self.require_live()
        plan = self.runtime.publications.plan(publication_id, allow_expired=allow_expired)
        with self.runtime.factory() as db:
            row = self.runtime.publications.require(db, publication_id)
            if row.manifest_hash != plan.manifest_hash:
                raise WorkflowError("CONFIRMATION_STALE", "发布包在提交前已改变。")
            manifest = PublicationManifest.model_validate_json(row.manifest_json)
        files = self.resolve_files(manifest)
        self.gateway.preflight(manifest, files)
        self.start_submission(publication_id, plan.manifest_hash, task)
        try:
            bvid = self.gateway.submit(manifest, files)
        except Exception as error:
            if isinstance(error, WorkflowError) and error.code in RATE_LIMIT_ERRORS:
                code = "137022" if error.code == "BILI_SUBMIT_RATE_LIMIT" else "601"
                self.defer_rejected(publication_id, plan.manifest_hash, code)
                raise
            with self.runtime.factory.begin() as db:
                pace_attempt(db, self.runtime.tasks.clock())
                if task and isinstance(error, WorkflowError):
                    hold_dependency(db, task, error, self.runtime.tasks.clock())
            self.set_status(publication_id, "unknown")
            raise WorkflowError("SUBMIT_RESULT_UNKNOWN", "投稿结果不明，未自动重试。") from None
        self.record_uploaded(
            publication_id, plan.manifest_hash, bvid, task.batch_id if task else None
        )
        return bvid

    def defer_rejected(self, publication_id: str, expected_hash: str, code: str = "601") -> None:
        with self.runtime.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = self.runtime.publications.require(db, publication_id)
            if row.status != "submitting" or row.remote_id or row.manifest_hash != expected_hash:
                raise WorkflowError("CONFIRMATION_STALE", "限流回执与投稿意图不符，未重置状态。")
            row.status = "queued"
            record_rejection(db, self.runtime.tasks.clock(), code)

    def record_uploaded(
        self, publication_id: str, expected_hash: str, bvid: str, batch_id: str | None
    ) -> None:
        with self.runtime.factory.begin() as db:
            row = self.runtime.publications.require(db, publication_id)
            if row.manifest_hash != expected_hash or row.confirmed_hash != expected_hash:
                raise WorkflowError("CONFIRMATION_STALE", "上传后发布包已改变，不自动挂载字幕。")
            row.status, row.remote_id = "uploaded", bvid
            clear_rejections(db, self.runtime.tasks.clock())
            task = add_task(
                db,
                "finalize_private",
                f"finalize:{publication_id}:{expected_hash}",
                stamp(self.runtime.tasks.clock()),
            )
            task.input_json = FinalizePrivate(
                publication_id=publication_id, manifest_hash=expected_hash
            ).model_dump_json()
            task.max_attempts = 60
            task.batch_id = batch_id

    def require_live(self) -> None:
        if not self.runtime.settings.publication_enabled:
            raise WorkflowError(
                "LIVE_PUBLICATION_DISABLED", "真实投稿未启用，本次仅可生成离线计划。"
            )

    def start_submission(
        self, publication_id: str, expected_hash: str, task: Task | None = None
    ) -> None:
        with self.runtime.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            require_upload_slot(db, publication_id, self.runtime.tasks.clock())
            row = self.runtime.publications.require(db, publication_id)
            manifest = PublicationManifest.model_validate_json(row.manifest_json)
            if task:
                self.validate_task(db, task, expected_hash)
            if (
                row.status not in {"confirmed", "queued"}
                or row.confirmed_hash != row.manifest_hash
                or row.manifest_hash != expected_hash
            ):
                raise WorkflowError("ALREADY_SUBMITTED", "发布包状态不允许再次提交。")
            if manifest.test_only or not manifest.rights_confirmed:
                raise WorkflowError(
                    "PRIVATE_UPLOAD_NOT_ELIGIBLE", "仅允许已确认使用权限及可见性的真实转载稿件。"
                )
            row.status = "submitting"
            pace_attempt(db, self.runtime.tasks.clock())

    def validate_task(self, db: Session, task: Task, expected_hash: str) -> None:
        if not self.runtime.tasks.owns(db.get(Task, task.id), task):
            raise WorkflowError("LEASE_EXPIRED", "投稿任务租约失效。")
        request = FinalizePrivate.model_validate_json(task.input_json)
        if request.manifest_hash != expected_hash:
            raise WorkflowError("CONFIRMATION_STALE", "任务与发布包摘要不符。")

    def resolve_files(self, manifest: PublicationManifest) -> UploadFiles:
        assets = self.runtime.artifacts
        return UploadFiles(
            media=assets.resolve(manifest.media_ref, "media"),
            cover=assets.resolve(manifest.cover_ref, "cover"),
            subtitle=assets.resolve(manifest.subtitle_ref, "subtitle")
            if manifest.subtitle_ref
            else None,
        )

    def recover_receipt(self, publication_id: str, remote_id: str) -> str:
        plan = self.runtime.publications.plan(publication_id)
        with self.runtime.factory() as db:
            row = self.runtime.publications.require(db, publication_id)
            manifest = PublicationManifest.model_validate_json(row.manifest_json)
            created = parse_stamp(row.created_at).timestamp()
        submission = self.gateway.inspect(remote_id)
        verify_remote_identity(manifest, submission)
        if (
            not created - 60 <= submission.archive.ctime <= now_utc().timestamp() + 300
            or len(submission.videos) != 1
            or submission.videos[0].duration <= 0
        ):
            raise WorkflowError("RECEIPT_NOT_VERIFIED", "稿件时间或分P信息不足以恢复回执。")
        with self.runtime.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = self.runtime.publications.require(db, publication_id)
            if (
                row.status not in {"unknown", "submitting"}
                or row.remote_id
                or row.manifest_hash != plan.manifest_hash
            ):
                raise WorkflowError("RECEIPT_RECOVERY_DENIED", "当前发布包不能绑定新的远端回执。")
            if db.scalar(select(Publication.id).where(Publication.remote_id == remote_id)):
                raise WorkflowError("RECEIPT_ALREADY_BOUND", "远端稿件已绑定其他发布记录。")
            row.remote_id, row.status = remote_id, "uploaded"
        return self.reconcile(publication_id)

    def resume_finalization(self, publication_id: str) -> str:
        self.require_live()
        _, submission, _ = self.remote_context(publication_id)
        with self.runtime.factory() as db:
            row = self.runtime.publications.require(db, publication_id)
            request = FinalizePrivate(
                publication_id=publication_id, manifest_hash=row.manifest_hash
            )
        return resume_recovered_upload(
            self.runtime.factory, request, submission.archive.bvid, self.runtime.tasks.clock()
        )

    def remote_context(
        self, publication_id: str
    ) -> tuple[PublicationManifest, RemoteSubmission, str]:
        with self.runtime.factory() as db:
            row = self.runtime.publications.require(db, publication_id)
            manifest = PublicationManifest.model_validate_json(row.manifest_json)
            remote_id, status = row.remote_id, row.status
            revision = active_revision(db, row)
            if revision:
                manifest = manifest.model_copy(update={"subtitle_ref": revision.revised_ref})
        if not remote_id:
            raise WorkflowError("REMOTE_ID_REQUIRED", "没有唯一远端稿件 ID，不能猜测或重新投稿。")
        submission = self.gateway.inspect(remote_id)
        verify_remote_identity(manifest, submission)
        return manifest, submission, status

    def reconcile(self, publication_id: str) -> str:
        manifest, submission, previous = self.remote_context(publication_id)
        if submission.archive.state_desc in REJECTED_LABELS:
            record_video_rejection(
                self.runtime.factory, publication_id, submission.archive, self.runtime.tasks.clock()
            )
            raise WorkflowError("VIDEO_REVIEW_REJECTED", "平台明确退回视频，保留回执，不重新投稿。")
        if not submission.archive.had_passed:
            status = "reviewing"
        elif manifest.subtitle_mode == "hard":
            allowed = {0, -50} if manifest.visibility == "private" else {0}
            if submission.archive.state not in allowed:
                raise WorkflowError(
                    "REMOTE_REVIEW_STATUS_UNKNOWN",
                    "当前视频状态不足以确认过审；未清理媒体。",
                    retryable=True,
                )
            if self.runtime.hard_subtitle_check is None:
                raise WorkflowError("HARD_SUBTITLES_NOT_VERIFIED", "缺少硬字幕烧录核验器。")
            seconds = self.runtime.hard_subtitle_check(manifest)
            if len(submission.videos) != 1 or abs(submission.videos[0].duration - seconds) > 1:
                raise WorkflowError("REMOTE_MEDIA_MISMATCH", "远端成片时长与烧录回执不一致。")
            status = "completed"
        elif manifest.subtitle_ref:
            subtitle = self.runtime.artifacts.resolve(manifest.subtitle_ref, "subtitle")
            verified = self.verify_caption(publication_id, submission, subtitle)
            status = "completed" if verified else subtitle_wait_status(previous)
        else:
            status = "completed"
        return self.set_status(publication_id, status)

    def attach_subtitles(self, publication_id: str) -> str:
        if not self.legacy_native_subtitles:
            raise WorkflowError(
                "NATIVE_SUBTITLES_DISABLED", "已停用B站原生字幕上传，请生成双语烧录成片。"
            )
        self.require_live()
        manifest, submission, previous = self.remote_context(publication_id)
        if manifest.subtitle_mode == "hard":
            raise WorkflowError("NATIVE_SUBTITLES_DISABLED", "硬字幕稿件不上传原生字幕。")
        if not submission.archive.had_passed or not manifest.subtitle_ref:
            raise WorkflowError("SUBTITLES_NOT_READY", "稿件未通过审核或没有字幕产物。")
        subtitle = self.runtime.artifacts.resolve(manifest.subtitle_ref, "subtitle")
        if self.verify_caption(publication_id, submission, subtitle):
            self.set_status(publication_id, "completed")
            return "completed"
        if previous == "subtitle_rejected":
            raise WorkflowError(
                "SUBTITLE_REVISION_REQUIRED", "该版本字幕已被退回，必须建立修订记录后再提交。"
            )
        if previous in {"subtitle_submitting", "subtitle_unknown"}:
            raise WorkflowError("SUBTITLE_RESULT_UNKNOWN", "此前字幕写入结果不明，禁止重复写入。")
        if self.runtime.subtitle_check:
            self.runtime.subtitle_check(subtitle)
        self.claim_subtitle_write(publication_id)
        try:
            verified = self.write_subtitle(publication_id, submission, subtitle)
        except Exception as error:
            if isinstance(error, WorkflowError) and error.code == "SUBTITLE_SAVE_REJECTED":
                self.set_status(publication_id, "subtitle_rejected")
                raise
            self.set_status(publication_id, "subtitle_unknown")
            raise WorkflowError(
                "SUBTITLE_RESULT_UNKNOWN", "字幕写入或回查未完成，未自动重试。"
            ) from None
        status = "completed" if verified else "subtitle_unknown"
        self.set_status(publication_id, status)
        return status

    def verify_caption(
        self, publication_id: str, submission: RemoteSubmission, subtitle: Path
    ) -> bool:
        try:
            return self.gateway.verify_subtitles(submission, subtitle)
        except WorkflowError as error:
            if error.code == "SUBTITLE_REVIEW_REJECTED":
                self.set_status(publication_id, "subtitle_rejected")
            raise

    def write_subtitle(
        self, publication_id: str, submission: RemoteSubmission, subtitle: Path
    ) -> bool:
        with self.runtime.factory() as db:
            revision = active_revision(db, self.runtime.publications.require(db, publication_id))
        if revision is None:
            return self.gateway.attach_and_verify(submission, subtitle)
        if not isinstance(self.gateway, SubtitleRevisionGateway):
            raise WorkflowError("SUBTITLE_REVISION_UNSUPPORTED", "字幕适配器不支持修订，未写入。")
        previous = self.runtime.artifacts.resolve(revision.previous_ref, "subtitle")
        return self.gateway.revise_subtitles(
            submission, previous, subtitle, revision.rejected_subtitle_id
        )

    def claim_subtitle_write(self, publication_id: str) -> None:
        with self.runtime.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = self.runtime.publications.require(db, publication_id)
            if row.status not in {"uploaded", "reviewing", "awaiting_subtitles"}:
                raise WorkflowError(
                    "SUBTITLE_WRITE_NOT_ALLOWED", "字幕已有写入意图，拒绝并发或重复写入。"
                )
            row.status = "subtitle_submitting"

    def set_status(self, publication_id: str, status: str, remote_id: str | None = None) -> str:
        with self.runtime.factory.begin() as db:
            row = self.runtime.publications.require(db, publication_id)
            if status in {"awaiting_subtitles", "subtitle_unknown", "reviewing"}:
                if row.status in {"subtitle_rejected", "withdrawn"}:
                    return row.status
                if row.status == "completed":
                    status = "subtitle_unknown"
            row.status = status
            if remote_id:
                row.remote_id = remote_id
            return row.status


def subtitle_wait_status(previous: str) -> str:
    if previous in {"subtitle_rejected", "withdrawn"}:
        return previous
    return (
        "subtitle_unknown"
        if previous in {"subtitle_unknown", "subtitle_submitting", "completed"}
        else "awaiting_subtitles"
    )


def verify_remote_identity(manifest: PublicationManifest, submission: RemoteSubmission) -> None:
    if (
        submission.archive.title != manifest.title
        or submission.archive.is_only_self != int(manifest.visibility == "private")
        or submission.archive.copyright != 2
        or submission.archive.source != manifest.source_url
    ):
        raise WorkflowError(
            "REMOTE_VERIFICATION_FAILED", "标题、转载来源或可见性与冻结发布包不符。"
        )
