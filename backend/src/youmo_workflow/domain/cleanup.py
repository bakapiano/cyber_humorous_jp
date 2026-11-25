from pydantic import Field

from youmo_workflow.domain.models import Contract


class CleanupFile(Contract):
    relative_path: str
    sha256: str
    size_bytes: int = Field(ge=0)


class CleanupPlan(Contract):
    batch_id: str
    publication_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    files: list[CleanupFile] = Field(default_factory=list[CleanupFile])
    planned: bool = False


class CleanupResult(Contract):
    batch_id: str
    video_files: int
    released_bytes: int
    llm_records_preserved: bool = True
