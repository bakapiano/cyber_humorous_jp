"""Typed subset used by the adapter, checked against faster-whisper 1.2.1.

Only public parameters actually called here are declared. Runtime segment data
is validated again through the domain Transcript model.
"""

from collections.abc import Iterable

class Segment:
    start: float
    end: float
    text: str

class TranscriptionInfo:
    duration: float

class WhisperModel:
    def __init__(
        self,
        model_size_or_path: str,
        *,
        device: str = "auto",
        compute_type: str = "default",
        local_files_only: bool = False,
    ) -> None: ...
    def transcribe(
        self,
        audio: str,
        *,
        language: str | None = None,
        beam_size: int = 5,
        vad_filter: bool = False,
        condition_on_previous_text: bool = True,
        without_timestamps: bool = False,
        word_timestamps: bool = False,
    ) -> tuple[Iterable[Segment], TranscriptionInfo]: ...
