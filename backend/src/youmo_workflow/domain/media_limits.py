import math

from youmo_workflow.domain.errors import WorkflowError

MIN_SOURCE_SECONDS = 20
MAX_SOURCE_SECONDS = 1800


class SourceTooLong(WorkflowError):
    def __init__(self, video_id: str, duration: float) -> None:
        if not math.isfinite(duration) or duration <= MAX_SOURCE_SECONDS:
            raise ValueError("An overlength decision requires a finite duration above the limit")
        super().__init__(
            "SOURCE_TOO_LONG", f"原片时长{duration:g}秒超过{MAX_SOURCE_SECONDS}秒上限，跳过该素材。"
        )
        self.video_id, self.duration = video_id, duration
