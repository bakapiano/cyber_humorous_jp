from pathlib import Path

import pytest
from conftest import Clock
from PIL import Image
from production_support import WORKSPACE, FixtureGeneration, prepared_content, transcript

from youmo_workflow.adapters.packaging import ProductionPackaging, packaging_directory
from youmo_workflow.adapters.production_files import folder, freeze
from youmo_workflow.adapters.production_profile import ProductionProfile
from youmo_workflow.adapters.raw_titles import title_input
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import PackagingOutput, PackagingRequest
from youmo_workflow.runtime import Runtime


@pytest.mark.parametrize("name", ["packaging_v004", "packaging_poster_v001"])
def test_completed_retired_packaging_remains_read_only(
    runtime: Runtime, clock: Clock, name: str
) -> None:
    content = prepared_content(runtime, clock)
    request = PackagingRequest(content=content)
    directory = folder(runtime.artifacts.root, content.content_id, name)
    cover = directory / "cover_A.png"
    Image.new("RGB", (1280, 720)).save(cover)
    expected = PackagingOutput(
        content_id=content.content_id,
        title="历史标题原文保持不变",
        cover_ref=runtime.artifacts.register(cover, "cover", test_only=True),
        original_media_ref=content.original_media_ref,
        packaging_version=name,
    )
    freeze(directory / "input.json", request.model_dump_json())
    freeze(directory / "output.json", expected.model_dump_json())
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    model = FixtureGeneration()
    provider = ProductionPackaging(runtime.artifacts, model, ProductionProfile(WORKSPACE))
    assert provider.package(request) == expected and not model.calls
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    content.evidence = ["HIDDEN_REFERENCE_ANSWER", "previous_generated_title", "selection_score"]
    payload = title_input(PackagingRequest(content=content), transcript())
    assert set(payload) == {"source_titles", "transcript"}
    assert payload["source_titles"] == [{"title": content.original_title}]
    assert payload["transcript"] == [
        {"start": row.start, "end": row.end, "text": row.text} for row in transcript().segments
    ]
    assert "HIDDEN_REFERENCE_ANSWER" not in str(payload)


@pytest.mark.parametrize("name", ["packaging_v004", "packaging_poster_v001"])
@pytest.mark.parametrize("file", ["input.json", "output.json"])
def test_retired_partial_packaging_requires_explicit_migration(
    tmp_path: Path, name: str, file: str
) -> None:
    legacy = folder(tmp_path, "synthetic", name)
    freeze(legacy / file, '{"historical":true}')
    with pytest.raises(WorkflowError, match="显式迁移"):
        packaging_directory(tmp_path, "synthetic")
    assert (legacy / file).read_text() == '{"historical":true}'
    assert not (legacy / "titles.json").exists()


def test_new_packaging_is_independent_and_conflicts_are_not_guessed(tmp_path: Path) -> None:
    current = packaging_directory(tmp_path, "synthetic")
    assert current.name == "packaging_raw_history_v001"
    assert not (current.parent / "packaging_v004").exists()
    freeze(current / "input.json", "{}")
    old = folder(tmp_path, "synthetic", "packaging_v004")
    freeze(old / "input.json", "{}")
    freeze(old / "output.json", "{}")
    with pytest.raises(WorkflowError, match="版本冲突"):
        packaging_directory(tmp_path, "synthetic")
