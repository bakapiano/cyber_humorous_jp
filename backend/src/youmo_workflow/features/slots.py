from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import Policy
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.time import BUSINESS_ZONE, now_utc, parse_stamp
from youmo_workflow.storage.models import Publication, PublishSlot


class SlotService:
    def __init__(
        self,
        factory: sessionmaker[Session],
        policy: Policy,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self.factory, self.policy, self.clock = factory, policy, clock

    def reserve(self, publication_id: str) -> str:
        local = self.clock().astimezone(BUSINESS_ZONE)
        day, time = local.date().isoformat(), local.strftime("%H:%M")
        if time not in self.policy.publish_slots:
            raise WorkflowError("NOT_PUBLICATION_SLOT", "尚未到发布时段或已错过本时段。")
        with self.factory.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            previous = db.scalar(
                select(PublishSlot).where(PublishSlot.publication_id == publication_id)
            )
            if previous:
                return previous.id
            row = db.get(Publication, publication_id)
            validate_reservation(row, self.clock())
            assert row is not None
            count = (
                db.scalar(
                    select(func.count())
                    .select_from(PublishSlot)
                    .where(PublishSlot.local_date == day)
                )
                or 0
            )
            occupied = db.scalar(
                select(PublishSlot).where(
                    PublishSlot.local_date == day, PublishSlot.slot_time == time
                )
            )
            if count >= self.policy.max_new_publications_per_day or occupied:
                raise WorkflowError("PUBLICATION_QUOTA_REACHED", "本时段或当天额度已占用。")
            slot = PublishSlot(
                id=uuid4().hex, local_date=day, slot_time=time, publication_id=publication_id
            )
            db.add(slot)
            row.status = "queued"
            return slot.id


def validate_reservation(row: Publication | None, at: datetime) -> None:
    if row is None or row.status != "confirmed" or row.confirmed_hash != row.manifest_hash:
        raise WorkflowError("CONFIRMATION_REQUIRED", "发布包没有有效确认。")
    manifest = PublicationManifest.model_validate_json(row.manifest_json)
    if manifest.visibility != "public" or manifest.test_only or not manifest.rights_confirmed:
        raise WorkflowError(
            "PUBLICATION_NOT_ELIGIBLE", "仅可为已确认使用权限的真实公开稿件预约时段。"
        )
    if parse_stamp(manifest.expires_at) <= at:
        raise WorkflowError("CONTENT_EXPIRED", "稿件已过期。")
