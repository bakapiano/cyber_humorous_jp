from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from youmo_workflow.domain.publication import PublicationManifest


class RemoteArchive(BaseModel):
    bvid: str
    title: str
    aid: int
    is_only_self: int
    copyright: int = 0
    source: str = ""
    ctime: int = 0
    had_passed: bool = False
    state: int | None = None
    state_desc: str = ""


class RemotePart(BaseModel):
    cid: int
    duration: float = 0


class RemoteSubmission(BaseModel):
    archive: RemoteArchive
    videos: list[RemotePart] = Field(default_factory=lambda: list[RemotePart]())


class UploadFiles(BaseModel):
    media: Path
    cover: Path
    subtitle: Path | None


class PublicationGateway(Protocol):
    def preflight(self, manifest: PublicationManifest, files: UploadFiles) -> None: ...

    def submit(self, manifest: PublicationManifest, files: UploadFiles) -> str: ...

    def inspect(self, bvid: str) -> RemoteSubmission: ...

    def attach_and_verify(self, submission: RemoteSubmission, subtitle: Path) -> bool: ...

    def verify_subtitles(self, submission: RemoteSubmission, subtitle: Path) -> bool: ...


@runtime_checkable
class SubtitleRevisionGateway(Protocol):
    def rejected_subtitle(
        self, submission: RemoteSubmission, previous: Path
    ) -> tuple[str, str] | None: ...

    def revise_subtitles(
        self, submission: RemoteSubmission, previous: Path, revised: Path, subtitle_id: str
    ) -> bool: ...
