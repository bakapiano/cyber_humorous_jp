from typing import Literal

from pydantic import Field

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.media_limits import MAX_SOURCE_SECONDS
from youmo_workflow.domain.models import Contract


class SourceDurationEvidence(Contract):
    video_id: str
    duration_seconds: float = Field(gt=MAX_SOURCE_SECONDS, allow_inf_nan=False)
    limit_seconds: int = MAX_SOURCE_SECONDS


DownloadFailureCode = Literal[
    "SOURCE_DOWNLOAD_UNAVAILABLE", "SOURCE_REGION_RESTRICTED", "SOURCE_DURATION_UNSUPPORTED"
]
DOWNLOAD_SKIP_ERRORS = frozenset(
    {"SOURCE_DOWNLOAD_UNAVAILABLE", "SOURCE_REGION_RESTRICTED", "SOURCE_DURATION_UNSUPPORTED"}
)


class SourceDownloadEvidence(Contract):
    video_id: str
    kind: Literal["download_failure"] = "download_failure"
    error_code: DownloadFailureCode
    message: str


class SkipIdentity(Contract):
    task_id: str
    batch_id: str
    input_sha256: str
    output_sha256: str
    attempts: int
    authorized_at: str
    publication_succeeded: Literal[False] = False


class SourceSkip(SourceDurationEvidence, SkipIdentity):
    error_code: Literal["SOURCE_TOO_LONG", "SOURCE_DURATION_UNSUPPORTED"]


class DownloadSkip(SourceDownloadEvidence, SkipIdentity):
    pass


class SourceDownloadFailed(WorkflowError):
    def __init__(self, video_id: str, error: WorkflowError) -> None:
        self.evidence = SourceDownloadEvidence.model_validate(
            {"video_id": video_id, "error_code": error.code, "message": error.message}
        )
        super().__init__(error.code, error.message)
