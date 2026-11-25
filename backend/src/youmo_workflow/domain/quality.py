from pathlib import Path
from typing import Protocol

from youmo_workflow.domain.providers import ArtifactRef
from youmo_workflow.domain.publication import ArtifactDTO, PublicationManifest


class ArtifactReader(Protocol):
    def resolve(self, reference: ArtifactRef, expected_kind: str) -> Path: ...

    def describe(self, artifact_id: str) -> ArtifactDTO: ...


class ArtifactStore(ArtifactReader, Protocol):
    root: Path

    def register(self, path: Path, kind: str, *, test_only: bool = False) -> ArtifactRef: ...


class ManifestVerifier(Protocol):
    def verify(self, manifest: PublicationManifest) -> None: ...
