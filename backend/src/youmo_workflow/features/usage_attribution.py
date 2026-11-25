import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.storage.models import Batch, Publication, Task, Video


class Binding(BaseModel):
    content_id: str | None = None
    video_id: str | None = None
    content: "Binding | None" = None
    items: list["Binding"] = Field(default_factory=list["Binding"])

    def contents(self) -> list[tuple[str, str]]:
        result = [(self.content_id, self.video_id)] if self.content_id and self.video_id else []
        if self.content:
            result.extend(self.content.contents())
        for item in self.items:
            result.extend(item.contents())
        return result


class DispatchBinding(BaseModel):
    video_ids: list[str]


class PublicationBinding(BaseModel):
    source_url: str
    title: str


class ReceiptBinding(BaseModel):
    publication_id: str | None = None


@dataclass
class WorkflowUsageContext:
    title: str
    status: str
    video_ids: set[str] = field(default_factory=set[str])
    delivered: set[str] = field(default_factory=set[str])


@dataclass
class UsageContext:
    contents: dict[str, tuple[str, str | None]] = field(
        default_factory=dict[str, tuple[str, str | None]]
    )
    video_titles: dict[str, str] = field(default_factory=dict[str, str])
    video_batches: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    workflows: dict[str, WorkflowUsageContext] = field(
        default_factory=dict[str, WorkflowUsageContext]
    )
    delivered_videos: set[str] = field(default_factory=set[str])

    def content(self, content_id: str) -> tuple[str, str | None]:
        if content_id in self.contents:
            return self.contents[content_id]
        match = re.match(r"^yt-([A-Za-z0-9_-]{11})(?:-|$)", content_id)
        video_id = match[1] if match else content_id
        batches = self.video_batches.get(video_id, set())
        return video_id, next(iter(batches)) if len(batches) == 1 else None

    def link(self, content_id: str, video_id: str, batch_id: str | None) -> None:
        self.contents.setdefault(content_id, (video_id, batch_id))
        if batch_id:
            self.video_batches.setdefault(video_id, set()).add(batch_id)
            if batch_id in self.workflows:
                self.workflows[batch_id].video_ids.add(video_id)


def usage_context(factory: sessionmaker[Session]) -> UsageContext:
    result = UsageContext()
    with factory() as db:
        result.video_titles = {
            identifier: title for identifier, title in db.execute(select(Video.id, Video.title))
        }
        for row in db.scalars(select(Batch)):
            result.workflows[row.id] = WorkflowUsageContext(
                f"{row.local_date} · {row.id[:8]}", row.status
            )
        rows = list(
            db.scalars(
                select(Task).where(Task.batch_id.is_not(None)).order_by(Task.created_at, Task.id)
            )
        )
        for row in rows:
            bind_task(result, row)
        publications = {row.id: row for row in db.scalars(select(Publication))}
        for row in rows:
            if row.kind == "publish" and row.status == "succeeded":
                bind_receipt(result, row, publications)
    return result


def bind_task(context: UsageContext, row: Task) -> None:
    if row.kind == "dispatch_production" and row.output_json and row.batch_id:
        try:
            dispatch = DispatchBinding.model_validate_json(row.output_json)
            for video_id in dispatch.video_ids:
                context.video_batches.setdefault(video_id, set()).add(row.batch_id)
                if row.batch_id in context.workflows:
                    context.workflows[row.batch_id].video_ids.add(video_id)
        except ValidationError:
            pass
    if row.kind not in {
        "prepare_video",
        "prepare_content",
        "packaging",
        "editing",
        "prepare_publication",
    }:
        return
    for raw in (row.input_json, row.output_json):
        if not raw:
            continue
        try:
            for content_id, video_id in Binding.model_validate_json(raw).contents():
                context.link(content_id, video_id, row.batch_id)
        except ValidationError:
            continue


def bind_receipt(context: UsageContext, task: Task, publications: dict[str, Publication]) -> None:
    try:
        identifier = ReceiptBinding.model_validate_json(task.input_json).publication_id
        publication = publications.get(identifier or "")
        if publication is None or not publication.remote_id or publication.status == "unknown":
            return
        data = PublicationBinding.model_validate_json(publication.manifest_json)
    except ValidationError:
        return
    url = urlparse(data.source_url)
    video_id = parse_qs(url.query).get("v", [url.path.strip("/")])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return
    context.link(publication.content_id, video_id, task.batch_id)
    context.video_titles[video_id] = data.title
    context.delivered_videos.add(video_id)
    if task.batch_id in context.workflows:
        context.workflows[task.batch_id].delivered.add(video_id)
