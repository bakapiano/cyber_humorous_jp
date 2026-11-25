import re
from datetime import datetime, timedelta

from sqlalchemy import String, func, select
from sqlalchemy.orm import Session, sessionmaker

from youmo_workflow.domain.time import BUSINESS_ZONE, parse_stamp, stamp
from youmo_workflow.domain.workspace_views import ActivityBucket, PublicationActivity
from youmo_workflow.storage.models import Publication, Task


def publication_activity(
    factory: sessionmaker[Session], hours: int, at: datetime
) -> PublicationActivity:
    since = at - timedelta(hours=hours)
    daily = hours > 72
    step = timedelta(days=1) if daily else timedelta(hours=1)
    first = bucket_start(since, daily)
    last = bucket_start(at, daily)
    buckets: dict[str, ActivityBucket] = {}
    cursor = first
    while cursor <= last:
        key = stamp(cursor)
        buckets[key] = ActivityBucket(
            start=key, label=cursor.strftime("%m-%d" if daily else "%m-%d %H:00")
        )
        cursor += step
    for delivered_at in delivery_times(factory, since, at):
        key = stamp(bucket_start(parse_stamp(delivered_at), daily))
        buckets[key].count += 1
    return PublicationActivity(
        since=stamp(since),
        until=stamp(at),
        interval="day" if daily else "hour",
        total=sum(bucket.count for bucket in buckets.values()),
        buckets=list(buckets.values()),
    )


def bucket_start(at: datetime, daily: bool) -> datetime:
    local = at.astimezone(BUSINESS_ZONE).replace(minute=0, second=0, microsecond=0)
    return local.replace(hour=0) if daily else local


def delivery_times(factory: sessionmaker[Session], since: datetime, until: datetime) -> list[str]:
    # Upload task completion, not publication preparation or remote review time.
    deliveries = (
        select(
            Publication.remote_id.label("bvid"),
            func.min(Task.updated_at, type_=String).label("delivered_at"),
        )
        .join(Task, func.json_extract(Task.input_json, "$.publication_id") == Publication.id)
        .where(
            Task.kind == "publish",
            Task.status == "succeeded",
            Publication.remote_id.is_not(None),
            Publication.status.not_in(["unknown", "submitting", "prepared", "confirmed", "queued"]),
            func.json_extract(Publication.manifest_json, "$.test_only") == 0,
            func.json_extract(Task.output_json, "$.remote_id") == Publication.remote_id,
        )
        .group_by(Publication.remote_id)
        .subquery()
    )
    query = select(deliveries.c.bvid, deliveries.c.delivered_at).where(
        func.julianday(deliveries.c.delivered_at) >= func.julianday(stamp(since)),
        func.julianday(deliveries.c.delivered_at) <= func.julianday(stamp(until)),
    )
    with factory() as db:
        return [
            str(at)
            for bvid, at in db.execute(query)
            if bvid and re.fullmatch(r"BV[A-Za-z0-9]{10}", str(bvid))
        ]
