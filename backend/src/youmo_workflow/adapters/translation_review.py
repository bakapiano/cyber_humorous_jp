from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from youmo_workflow.adapters.production_files import encode, freeze
from youmo_workflow.adapters.subtitle_review import load_rules
from youmo_workflow.domain.content import GenerationRequest, Transcript
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.subtitle_filter import WordFilter


@dataclass
class TranslationReview:
    rules_path: Path
    replay: Callable[[GenerationRequest], str | None]

    def prepare(self, transcript: Transcript, directory: Path) -> tuple[Transcript, WordFilter]:
        rules, digest = load_rules(self.rules_path)
        words = WordFilter(rules)
        texts, counts = words.clean([row.text for row in transcript.segments])
        segments = [
            row.model_copy(update={"text": text})
            for row, text in zip(transcript.segments, texts, strict=True)
        ]
        filtered = transcript.model_copy(update={"segments": segments})
        freeze(directory / "translation-review-v003/rules.json", rules.model_dump_json())
        freeze(directory / "translation-review-v003/input.json", filtered.model_dump_json())
        freeze(
            directory / "translation-review-v003/audit.json",
            encode(
                {
                    "policy_sha256": digest,
                    "replacement": "mask",
                    "matched_rules": {key: value for key, value in counts.items()},
                    "input_segments": len(transcript.segments),
                    "output_segments": len(segments),
                    "raw_transcript_unchanged": True,
                }
            ),
        )
        return filtered, words

    def legacy_input(self, transcript: Transcript, directory: Path) -> Transcript | None:
        path = directory / "translation-review/input.json"
        if not path.exists():
            return None
        previous = Transcript.model_validate_json(path.read_bytes())
        if (
            previous.video_id != transcript.video_id
            or previous.source_sha256 != transcript.source_sha256
        ):
            raise WorkflowError("PRODUCTION_INPUT_CHANGED", "旧翻译输入与原片身份不一致。")
        return previous
