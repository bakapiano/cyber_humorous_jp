import hashlib
from pathlib import Path

import pysubs2
from pydantic import ValidationError

from youmo_workflow.adapters.production_files import digest, encode, freeze, save
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.providers import ArtifactRef
from youmo_workflow.domain.publication import PublicationManifest
from youmo_workflow.domain.quality import ArtifactStore
from youmo_workflow.domain.subtitle_filter import SubtitleRules, WordFilter


def load_rules(path: Path) -> tuple[SubtitleRules, str]:
    try:
        raw = path.read_bytes()
        if len(raw) > 4000000:
            raise WorkflowError("SUBTITLE_RULE_INVALID", "字幕词库超过大小限制。")
        rules = SubtitleRules.model_validate_json(raw)
        WordFilter(rules)
        return rules, hashlib.sha256(raw).hexdigest()
    except (OSError, ValidationError):
        raise WorkflowError(
            "SUBTITLE_RULE_INVALID", "字幕禁词库缺失或格式无效，未绕过审查。"
        ) from None


class SubtitleReviewer:
    def __init__(self, assets: ArtifactStore, rules_path: Path) -> None:
        self.assets, self.rules_path = assets, rules_path

    def prepare(self, manifest: PublicationManifest) -> PublicationManifest:
        if not manifest.subtitle_ref:
            return manifest
        source = self.assets.resolve(manifest.subtitle_ref, "subtitle")
        if manifest.subtitle_mode == "hard":
            # A changed sidecar would not change pixels already burned into media.
            self.verify(source)
            return manifest
        rules, policy_hash = load_rules(self.rules_path)
        original = pysubs2.load(str(source), encoding="utf-8")
        texts, counts = WordFilter(rules).clean_cues([event.plaintext for event in original.events])
        kept = [
            (event, text)
            for event, text in zip(original.events, texts, strict=True)
            if text.strip()
        ]
        if not kept:
            raise WorkflowError("SUBTITLE_EMPTY", "字幕为空，停止投稿并保留原始资料。")
        directory = self.assets.root / "subtitle_reviews" / f"{digest(source)}-{policy_hash}"
        freeze(directory / "rules.json", rules.model_dump_json())
        changed = any(
            event.plaintext != text for event, text in zip(original.events, texts, strict=True)
        )
        target = self.filtered_file(directory, kept) if changed else source
        self.verify(target)
        save(
            directory / "audit.json",
            encode(
                {
                    "policy_sha256": policy_hash,
                    "replacement": "mask",
                    "input_sha256": digest(source),
                    "output_sha256": digest(target),
                    "matched_rules": {key: value for key, value in counts.items()},
                    "input_cues": len(original.events),
                    "output_cues": len(kept),
                    "raw_records_unchanged": True,
                    "remaining_matches": 0,
                }
            ),
        )
        if not changed:
            return manifest
        test_only = self.assets.describe(manifest.subtitle_ref.artifact_id).test_only
        ref = self.reference(directory, target, test_only)
        return manifest.model_copy(update={"subtitle_ref": ref})

    def reference(self, directory: Path, target: Path, test_only: bool) -> ArtifactRef:
        cached = directory / f"reference-{test_only}.json"
        if cached.exists():
            ref = ArtifactRef.model_validate_json(cached.read_bytes())
            resolved = self.assets.resolve(ref, "subtitle")
            if (
                resolved != target.resolve()
                or self.assets.describe(ref.artifact_id).test_only != test_only
            ):
                raise WorkflowError("SUBTITLE_REVIEW_CHANGED", "审查字幕引用不一致。")
            return ref
        ref = self.assets.register(target, "subtitle", test_only=test_only)
        freeze(cached, ref.model_dump_json())
        return ref

    def filtered_file(self, directory: Path, events: list[tuple[pysubs2.SSAEvent, str]]) -> Path:
        subtitles = pysubs2.SSAFile()
        for event, text in events:
            item = pysubs2.SSAEvent(start=event.start, end=event.end)
            item.plaintext = text
            subtitles.events.append(item)
        target = directory / "masked.zh-ja.srt"
        freeze(target, subtitles.to_string("srt"))
        return target

    def verify(self, path: Path) -> None:
        rules, _ = load_rules(self.rules_path)
        subtitles = pysubs2.load(str(path), encoding="utf-8")
        _, counts = WordFilter(rules).clean_cues([event.plaintext for event in subtitles.events])
        if counts:
            raise WorkflowError(
                "SUBTITLE_DENYLIST_MATCH", "上传字幕仍命中禁词，已阻止投稿或字幕写入。"
            )
