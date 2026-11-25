import json
from collections.abc import Callable
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import select, true
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.editing import EditedContent, EditingProvider
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import VideoDTO
from youmo_workflow.domain.providers import (
    ChannelProvider,
    HomeProvider,
    PackagingProvider,
    PackagingRequest,
    ReadyContent,
    SelectedContent,
    SelectionOutput,
    SelectionProvider,
    SelectionRequest,
)
from youmo_workflow.domain.publication import PublicationDTO, PublicationManifest
from youmo_workflow.domain.selection import SingleContentProvider
from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.features.artifacts import ArtifactService
from youmo_workflow.features.batch_plan import batch_editing_revision
from youmo_workflow.features.batch_progress import refresh_batch_progress
from youmo_workflow.features.batches import BatchService
from youmo_workflow.features.publications import PublicationService
from youmo_workflow.features.sources import SourceService
from youmo_workflow.features.tasks import TaskService, add_task
from youmo_workflow.orchestration.batch_dispatch import BatchDispatcher
from youmo_workflow.orchestration.selection import SelectionStages
from youmo_workflow.storage.models import BatchItem, Task, Video


class SyncInput(BaseModel):
    source_id: str
    window_hours: int = 72


class Providers:
    def __init__(
        self,
        channel: ChannelProvider,
        selection: SelectionProvider,
        packaging: PackagingProvider,
        publications: PublicationService,
    ) -> None:
        self.channel = channel
        self.selection = selection
        self.packaging = packaging
        self.publications = publications
        self.home: HomeProvider | None = None
        self.editor: EditingProvider | None = None
        self.content: SingleContentProvider | None = None


class Runner:
    def __init__(
        self,
        factory: sessionmaker[Session],
        providers: Providers,
        artifacts: ArtifactService,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.factory, self.providers, self.artifacts, self.clock = (
            factory,
            providers,
            artifacts,
            clock,
        )
        self.tasks = TaskService(factory, clock)
        self.sources = SourceService(factory, clock)
        self.batches = BatchService(factory, clock)
        self.known_channels: dict[str, str] = {}
        self.selection_stages: SelectionStages | None = None
        self.finalize_private: Callable[[Task], str] | None = None
        self.publish_private: Callable[[Task], str] | None = None
        self.authorize_publication: Callable[[Task, PublicationDTO], None] | None = None
        self.task_kinds: tuple[str, ...] | None = None
        self.downstream_first = False
        self.cleanup_batch: Callable[[Task], str] | None = None
        self.last_task: Task | None = None

    def once(self) -> bool:
        self.tasks.recover()
        task = self.tasks.claim(self.task_kinds, downstream_first=self.downstream_first)
        self.last_task = task
        if task is None:
            return False
        try:
            output = self.dispatch(task)
            self.tasks.finish(task, output)
        except WorkflowError as error:
            self.fail(task, error)
        except Exception:
            error = WorkflowError(
                "INTERNAL_ERROR", "任务执行异常；详细诊断需离线复现，未泄露凭据。"
            )
            self.fail(task, error)
        refresh_batch_progress(self.factory, task.batch_id, self.clock())
        return True

    def fail(self, task: Task, error: WorkflowError) -> None:
        if self.tasks.fail(task, error) and task.batch_id:
            self.batches.set_result(task.batch_id, "failed", error)

    def dispatch(self, task: Task) -> str:
        if task.kind in {"initial_selection", "rescreen", "prepare_content"}:
            if self.selection_stages is None:
                raise WorkflowError("INITIAL_SELECTION_NOT_CONFIGURED", "初筛流水线尚未配置。")
            return self.selection_stages.run(task)
        handlers: dict[str, Callable[[Task], str] | None] = {
            "cleanup_batch": self.cleanup_batch,
            "cleanup_publication": self.cleanup_batch,
            "dispatch_production": BatchDispatcher(self.factory, self.clock).run,
            "publish": self.publish_private,
            "finalize_private": self.finalize_private,
            "sync_home": self.sync_home,
            "sync_source": self.sync_source,
            "selection": self.select,
            "packaging": self.package,
            "prepare_video": self.prepare_video,
            "editing": self.edit,
            "prepare_publication": self.prepare,
        }
        handler = handlers.get(task.kind)
        if handler is None:
            raise WorkflowError("UNKNOWN_TASK", "任务类型不受支持或执行器未配置。")
        return handler(task)

    def sync_source(self, task: Task) -> str:
        request = SyncInput.model_validate_json(task.input_json)
        boundary = stamp(self.clock())
        since = stamp(self.clock() - timedelta(hours=request.window_hours))
        try:
            items = self.providers.channel.collect(request.source_id, since)
            added = self.sources.ingest(request.source_id, items, boundary)
        except WorkflowError as error:
            self.sources.record_failure(request.source_id, error.message)
            raise
        return json.dumps({"new_videos": added, "received_videos": len(items)})

    def sync_home(self, _task: Task) -> str:
        if self.providers.home is None:
            raise WorkflowError("HOME_NOT_CONFIGURED", "推荐来源适配尚未配置。")
        page = self.providers.home.discover()
        items = self.providers.channel.lookup([item.video_id for item in page.items])
        result = self.sources.ingest_recommendations(items, self.known_channels)
        return json.dumps(
            {**result, "discovered": len(page.items), "metadata_resolved": len(items)}
        )

    def select(self, task: Task) -> str:
        if task.batch_id is None:
            raise WorkflowError("BATCH_REQUIRED", "选题任务缺少批次。")
        batch = self.batches.detail(task.batch_id)
        self.batches.set_result(batch.id, "running")
        request = SelectionRequest(
            batch_id=batch.id,
            items=batch.items,
            candidate_limit=batch.policy.candidate_limit,
            max_approved=batch.policy.max_approved,
        )
        output = self.providers.selection.select(request)
        validate_selection(request, output)
        self.save_selection(task, output)
        if not output.selected:
            self.batches.set_result(batch.id, "completed")
        return output.model_dump_json()

    def save_selection(self, task: Task, output: SelectionOutput) -> None:
        with self.factory.begin() as db:
            row = db.get(Task, task.id)
            if not self.tasks.owns(row, task):
                raise WorkflowError("LEASE_EXPIRED", "任务租约已失效，拒绝旧结果。")
            ids = db.scalars(
                select(BatchItem.video_id).where(BatchItem.batch_id == output.batch_id)
            )
            for video_id in ids:
                video = db.get(Video, video_id)
                if video:
                    video.screened = True
            for content in output.selected:
                child = add_task(
                    db, "packaging", f"packaging:{content.content_id}", stamp(self.clock())
                )
                child.batch_id, child.input_json = output.batch_id, content.model_dump_json()
            assert row is not None
            row.output_json, row.status = output.model_dump_json(), "succeeded"
            row.lease_token = row.lease_until = None

    def prepare_video(self, task: Task) -> str:
        if self.providers.content is None:
            raise WorkflowError("CONTENT_NOT_CONFIGURED", "单条内容准备未配置。")
        video = VideoDTO.model_validate_json(task.input_json)
        output = self.providers.content.prepare_video(video)
        if output.video_id != video.video_id or output.original_title != video.title:
            raise WorkflowError("CONTENT_ID_CHANGED", "内容准备修改了来源身份。")
        with self.factory.begin() as db:
            row = db.get(Task, task.id)
            if not self.tasks.owns(row, task):
                raise WorkflowError("LEASE_EXPIRED", "内容准备租约已失效。")
            child = add_task(db, "packaging", f"packaging:{output.content_id}", stamp(self.clock()))
            child.batch_id, child.input_json = task.batch_id, output.model_dump_json()
            assert row is not None
            row.output_json, row.status = output.model_dump_json(), "succeeded"
            row.lease_token = row.lease_until = None
        return output.model_dump_json()

    def package(self, task: Task) -> str:
        content = SelectedContent.model_validate_json(task.input_json)
        output = self.providers.packaging.package(PackagingRequest(content=content))
        if (
            output.content_id != content.content_id
            or output.original_media_ref != content.original_media_ref
        ):
            raise WorkflowError("PACKAGING_REFERENCE_MISMATCH", "包装结果更改了内容或原片引用。")
        self.artifacts.resolve(output.cover_ref, "cover")
        self.artifacts.resolve(output.original_media_ref, "media")
        with self.factory.begin() as db:
            row = db.get(Task, task.id)
            if not self.tasks.owns(row, task):
                raise WorkflowError("LEASE_EXPIRED", "任务租约已失效。")
            ready = ReadyContent(
                content=content,
                packaging=output,
                editing_revision=batch_editing_revision(db, task.batch_id),
            )
            key_version = "v005" if ready.editing_revision == "v005" else "v003"
            child = add_task(
                db, "editing", f"editing:{key_version}:{content.content_id}", stamp(self.clock())
            )
            child.batch_id, child.input_json = task.batch_id, ready.model_dump_json()
            assert row is not None
            row.output_json, row.status = ready.model_dump_json(), "succeeded"
            row.lease_token = row.lease_until = None
        return ready.model_dump_json()

    def edit(self, task: Task) -> str:
        if self.providers.editor is None:
            raise WorkflowError("EDITING_NOT_CONFIGURED", "剪辑未配置，不用原片代替成片。")
        ready = ReadyContent.model_validate_json(task.input_json)
        edited = self.providers.editor.edit(ready)
        if (
            edited.content_id != ready.content.content_id
            or edited.original_media_ref != ready.content.original_media_ref
            or edited.title != ready.packaging.title
        ):
            raise WorkflowError("EDITING_REFERENCE_MISMATCH", "剪辑修改了标题或原片身份。")
        self.artifacts.resolve(edited.media_ref, "media")
        self.artifacts.resolve(edited.subtitle_ref, "subtitle")
        self.artifacts.resolve(edited.plan_ref, "edit_plan")
        output = EditedContent(**ready.model_dump(), editing=edited)
        with self.factory.begin() as db:
            row = db.get(Task, task.id)
            if not self.tasks.owns(row, task):
                raise WorkflowError("LEASE_EXPIRED", "剪辑任务租约已失效。")
            child = add_task(
                db, "prepare_publication", f"prepare:{edited.content_id}", stamp(self.clock())
            )
            child.batch_id, child.input_json = task.batch_id, output.model_dump_json()
            assert row is not None
            row.output_json, row.status = output.model_dump_json(), "succeeded"
            row.lease_token = row.lease_until = None
        return output.model_dump_json()

    def prepare(self, task: Task) -> str:
        ready = EditedContent.model_validate_json(task.input_json)
        content, packaging = ready.content, ready.packaging
        is_test = self.artifacts.describe(ready.editing.media_ref.artifact_id).test_only
        manifest = PublicationManifest(
            content_id=content.content_id,
            title=packaging.title,
            media_ref=ready.editing.media_ref,
            cover_ref=packaging.cover_ref,
            subtitle_ref=ready.editing.subtitle_ref,
            subtitle_mode=ready.editing.subtitle_mode,
            burn_in_ref=ready.editing.burn_in_ref,
            source_url=f"https://www.youtube.com/watch?v={content.video_id}",
            expires_at=content.expires_at,
            test_only=is_test,
        )
        result = self.providers.publications.prepare(manifest)
        if self.authorize_publication:
            self.authorize_publication(task, result)
        self.complete_if_last(task)
        return result.model_dump_json()

    def complete_if_last(self, task: Task) -> None:
        if not task.batch_id:
            return
        with self.factory() as db:
            has_initial = db.scalar(
                select(Task.id).where(
                    Task.batch_id == task.batch_id, Task.kind == "initial_selection"
                )
            )
            has_v008 = db.scalar(
                select(Task.id).where(Task.unique_key == f"initial:v008:{task.batch_id}")
            )
            other = db.scalar(
                select(Task)
                .where(
                    Task.batch_id == task.batch_id, Task.id != task.id, Task.status != "succeeded"
                )
                .where(Task.kind != "selection" if has_initial else true())
                .where(Task.unique_key.not_like("%:v004:%") if has_v008 else true())
                .limit(1)
            )
        if other is None:
            self.batches.set_result(task.batch_id, "ready")


def validate_selection(request: SelectionRequest, result: SelectionOutput) -> None:
    ids = {item.video_id for item in request.items}
    selected = [item.video_id for item in result.selected]
    returned = selected + result.rejected_ids
    invalid = (
        result.batch_id != request.batch_id
        or len(selected) > request.max_approved
        or len(returned) != len(set(returned))
        or set(returned) != ids
        or len({item.content_id for item in result.selected}) != len(selected)
    )
    if invalid:
        raise WorkflowError(
            "INVALID_SELECTION_OUTPUT", "黑盒结果的批次、数量或来源身份不符合契约。"
        )
