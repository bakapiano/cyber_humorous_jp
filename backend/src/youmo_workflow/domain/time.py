from datetime import UTC, datetime
from zoneinfo import ZoneInfo

BUSINESS_ZONE = ZoneInfo("Asia/Shanghai")


def now_utc() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Timezone-aware time required")
    return value.astimezone(UTC).isoformat(timespec="seconds")


def parse_stamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone-aware time required")
    return result.astimezone(UTC)
