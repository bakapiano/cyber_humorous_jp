"""Logical queue ownership; consumers share the same bounded resource pool."""

SELECTION_KINDS = ("selection", "initial_selection", "rescreen", "dispatch_production")
PREPARATION_KINDS = ("prepare_content", "prepare_video")
PACKAGING_KINDS = ("packaging",)
EDITING_KINDS = ("editing", "prepare_publication")
MODEL_KINDS = (
    "selection",
    "initial_selection",
    "prepare_content",
    "prepare_video",
    "packaging",
    "editing",
)
PRODUCTION_KINDS = SELECTION_KINDS + PREPARATION_KINDS + PACKAGING_KINDS + EDITING_KINDS


def stage_assignments(workers: int) -> list[tuple[str, ...]]:
    if workers < 1:
        raise ValueError("At least one consumer is required")
    groups: tuple[tuple[str, ...], ...] = (
        SELECTION_KINDS,
        PREPARATION_KINDS,
        PACKAGING_KINDS,
        EDITING_KINDS,
    )
    if workers < len(groups):
        return [sum(groups[index::workers], ()) for index in range(workers)]
    # One selection consumer, independent capacity for all per-video stages.
    assignments: list[tuple[str, ...]] = [SELECTION_KINDS]
    assignments.extend(groups[1 + index % 3] for index in range(workers - 1))
    return assignments
