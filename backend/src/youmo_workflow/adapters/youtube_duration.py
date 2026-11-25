import re


def duration_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    found = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value)
    if not found or not any(part is not None for part in found.groups()):
        raise ValueError("Invalid YouTube duration")
    return sum(
        int(part or 0) * unit
        for part, unit in zip(found.groups(), (86400, 3600, 60, 1), strict=True)
    )
