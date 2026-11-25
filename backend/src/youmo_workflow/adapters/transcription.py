import os
import site
import sys
from functools import lru_cache
from pathlib import Path

from faster_whisper import WhisperModel

from youmo_workflow.adapters.media import probe
from youmo_workflow.adapters.production_files import digest, encode, save
from youmo_workflow.domain.content import Segment, Transcript
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.models import VideoDTO
from youmo_workflow.execution_limits import limited

DLL_HANDLES: list[object] = []


@lru_cache(maxsize=1)
def local_model() -> WhisperModel:
    for root in [*site.getsitepackages(), str(Path(sys.base_prefix) / "Lib/site-packages")]:
        for component in ("cublas", "cudnn", "cuda_nvrtc"):
            directory = Path(root) / "nvidia" / component / "bin"
            if directory.is_dir() and os.name == "nt":
                os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
                DLL_HANDLES.append(os.add_dll_directory(str(directory)))
    device = os.environ.get("WORKFLOW_ASR_DEVICE", "cuda")
    return WhisperModel(
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        device=device,
        compute_type="int8_float16" if device == "cuda" else "int8",
        local_files_only=True,
    )


class LocalTranscription:
    @limited("asr")
    def transcribe(self, video: VideoDTO, media: Path, directory: Path) -> Transcript:
        path = directory / "transcript.json"
        sha = digest(media)
        if path.exists():
            result = Transcript.model_validate_json(path.read_bytes())
            if result.source_sha256 != sha or result.video_id != video.video_id:
                raise WorkflowError("TRANSCRIPT_SOURCE_CHANGED", "缓存字幕与原片身份不一致。")
            return result
        duration = probe(media).duration_seconds
        try:
            segments, _ = local_model().transcribe(
                str(media),
                language="ja",
                beam_size=5,
                vad_filter=True,
                condition_on_previous_text=False,
                without_timestamps=False,
                # Faster Whisper uses the first/last aligned words as segment boundaries.
                # Whole-segment timestamps can begin at the preceding line's end.
                word_timestamps=True,
            )
            raw = [
                Segment(segment_id=f"T{index:04}", start=row.start, end=row.end, text=row.text)
                for index, row in enumerate(segments, 1)
            ]
        except Exception:
            raise WorkflowError(
                "LOCAL_ASR_UNAVAILABLE", "本地ASR模型或执行失败；未上传音频给远端。"
            ) from None
        save(directory / "asr_raw.json", encode([row.model_dump(mode="json") for row in raw]))
        normalized, previous = list[Segment](), 0.0
        for row in raw:
            start, end = max(previous, row.start), min(row.end, duration)
            if start < end and row.text.strip():
                normalized.append(row.model_copy(update={"start": start, "end": end}))
                previous = end
        result = Transcript(
            video_id=video.video_id,
            source_sha256=sha,
            duration_seconds=duration,
            segments=normalized,
        )
        save(path, result.model_dump_json())
        return result
