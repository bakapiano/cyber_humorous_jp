from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from youmo_workflow.domain.models import Contract, VideoDTO


class Segment(Contract):
    segment_id: str
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: str = Field(min_length=1)


class Transcript(Contract):
    video_id: str
    source_kind: Literal["youtube_original"] = "youtube_original"
    source_sha256: str
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    language: Literal["ja"] = "ja"
    segments: list[Segment] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_timeline(self) -> "Transcript":
        previous = 0.0
        ids: set[str] = set()
        for row in self.segments:
            if (
                row.segment_id in ids
                or not previous <= row.start < row.end <= self.duration_seconds
            ):
                raise ValueError("Invalid transcript timeline or duplicate segment")
            previous = row.end
            ids.add(row.segment_id)
        return self


class Citation(Contract):
    segment_id: str
    quote: str = Field(min_length=1)
    claim: str = ""
    support_note: str = ""


class AcquisitionProvider(Protocol):
    def acquire(self, video: VideoDTO, directory: Path) -> Path: ...


class TranscriptionProvider(Protocol):
    def transcribe(self, video: VideoDTO, media: Path, directory: Path) -> Transcript: ...


class ModelImage(Contract):
    label: str
    path: Path
    sha256: str


class GenerationRequest(Contract):
    content_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    stage: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    prompt: str
    images: list[ModelImage] = Field(default_factory=list[ModelImage])


class GenerationProvider(Protocol):
    def generate(self, request: GenerationRequest) -> str: ...
