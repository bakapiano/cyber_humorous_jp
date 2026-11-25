from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from pytest import MonkeyPatch

from youmo_workflow.adapters import transcription
from youmo_workflow.domain.models import VideoDTO


def test_transcription_uses_word_boundaries_without_rewriting_cached_output(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    def decode(_audio: str, *, word_timestamps: bool = False, **_options: object) -> object:
        start, end = (211.89, 214.43) if word_timestamps else (198.59, 214.37)
        return [SimpleNamespace(start=start, end=end, text="給与額について聞いてみると")], None

    model = Mock(transcribe=Mock(side_effect=decode))
    monkeypatch.setattr(transcription, "local_model", lambda: model)
    monkeypatch.setattr(
        transcription, "probe", Mock(return_value=SimpleNamespace(duration_seconds=230))
    )
    media = tmp_path / "source.mp4"
    media.write_bytes(b"isolated transcription fixture")
    video = VideoDTO(
        video_id="abcdefghijk",
        title="test source",
        source_id="test",
        discovered_at="2026-11-22T00:00:00Z",
        revision=1,
        screened=False,
    )

    adapter = transcription.LocalTranscription()
    directory = tmp_path / "transcription"
    result = adapter.transcribe(video, media, directory)
    assert [(row.start, row.end, row.text) for row in result.segments] == [
        (211.89, 214.43, "給与額について聞いてみると")
    ]
    assert adapter.transcribe(video, media, directory) == result
    model.transcribe.assert_called_once()
