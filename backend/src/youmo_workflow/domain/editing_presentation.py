"""Deterministic presentation only; never edits model-provided source intervals."""

from youmo_workflow.domain.editing import RangePlan, TimelinePart
from youmo_workflow.domain.models import Contract

OPENING_SECONDS = 10.0
TRANSITION_SECONDS = 0.6


class EditPresentation(Contract):
    opening_seconds: float = OPENING_SECONDS
    recap_skipped: bool
    transition_seconds: float
    timeline: list[TimelinePart]


def present_edit(plan: RangePlan) -> EditPresentation:
    skip = any(max(clip.start, 0) < min(clip.end, OPENING_SECONDS) for clip in plan.quick_recap)
    parts: list[TimelinePart] = []
    cursor = 0.0
    if not skip:
        recap = plan.quick_recap[0]
        cursor = round(recap.end - recap.start, 6)
        parts.append(
            TimelinePart(
                part="quick_recap",
                source_start=recap.start,
                source_end=recap.end,
                output_start=0,
                output_end=cursor,
            )
        )
        cursor = round(cursor + TRANSITION_SECONDS, 6)
    parts.append(
        TimelinePart(
            part="main",
            source_start=plan.main.start,
            source_end=plan.main.end,
            output_start=cursor,
            output_end=round(cursor + plan.main.end - plan.main.start, 6),
        )
    )
    return EditPresentation(
        recap_skipped=skip, transition_seconds=0 if skip else TRANSITION_SECONDS, timeline=parts
    )
