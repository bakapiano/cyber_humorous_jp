"""Persist explicit creator-provided rejection, never infer it from absent/pending data."""

from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.remote import RemoteArchive
from youmo_workflow.domain.time import stamp
from youmo_workflow.storage.models import Publication, WorkflowSetting

PREFIX = "video_review_rejection:"
REJECTED_LABELS = {
    "审核不通过",
    "审核未通过",
    "稿件审核未通过",
    "审核退回",
    "稿件打回",
    "未通过",
    "不通过",
}


class RejectedVideo(Contract):
    publication_id: str
    manifest_hash: str
    bvid: str
    state: int | None
    description: str
    observed_at: str


def record_video_rejection(
    factory: sessionmaker[Session], publication_id: str, archive: RemoteArchive, at: datetime
) -> None:
    with factory.begin() as db:
        row = db.get(Publication, publication_id)
        if (
            row is None
            or row.remote_id != archive.bvid
            or archive.state_desc not in REJECTED_LABELS
        ):
            raise WorkflowError("VIDEO_REVIEW_UNVERIFIED", "没有匹配的明确审核拒绝，不能跳过稿件。")
        evidence = RejectedVideo(
            publication_id=row.id,
            manifest_hash=row.manifest_hash,
            bvid=archive.bvid,
            state=archive.state,
            description=archive.state_desc,
            observed_at=stamp(at),
        )
        key = PREFIX + row.id + ":" + row.manifest_hash
        if not db.get(WorkflowSetting, key):
            db.add(WorkflowSetting(id=key, revision=1, value_json=evidence.model_dump_json()))
        row.status = "video_rejected"


def verify_video_rejection(db: Session, publication: Publication) -> None:
    row = db.get(WorkflowSetting, PREFIX + publication.id + ":" + publication.manifest_hash)
    if row is None:
        raise WorkflowError("VIDEO_REVIEW_UNVERIFIED", "没有保留平台审核拒绝回执。")
    evidence = RejectedVideo.model_validate_json(row.value_json)
    if (
        evidence.bvid != publication.remote_id
        or evidence.manifest_hash != publication.manifest_hash
        or evidence.publication_id != publication.id
        or evidence.description not in REJECTED_LABELS
        or publication.status != "video_rejected"
    ):
        raise WorkflowError("VIDEO_REVIEW_UNVERIFIED", "审核拒绝回执与发布包不一致。")
