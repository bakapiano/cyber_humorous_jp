from dataclasses import replace

import pysubs2
import pytest
from conftest import Assets, Clock
from test_publications import manifest
from test_remote import FakeGateway

from youmo_workflow.adapters.quality import QualityGate
from youmo_workflow.adapters.subtitle_review import SubtitleReviewer, load_rules
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.subtitle_filter import DenyRule, WordFilter
from youmo_workflow.features.publications import PublicationService
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime


def test_review_is_derived_idempotent_and_preserves_original(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    original = runtime.artifacts.root / "sensitive.srt"
    original.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n习近平发言\n習近平発言\n\n2\n00:00:01,000 --> 00:00:01,900\n法轮功\n法輪功\n",
        encoding="utf-8",
    )
    before = original.read_bytes()
    value = manifest(assets, clock)
    value.subtitle_ref = runtime.artifacts.register(original, "subtitle", test_only=True)
    reviewer = SubtitleReviewer(
        runtime.artifacts, runtime.settings.root / "config/subtitle-denylist.json"
    )
    filtered = reviewer.prepare(value)
    assert reviewer.prepare(value) == filtered
    assert reviewer.prepare(filtered) == filtered
    assert filtered.subtitle_ref is not None
    path = runtime.artifacts.resolve(filtered.subtitle_ref, "subtitle")
    cues = pysubs2.load(str(path))
    assert len(cues) == 2 and cues[0].plaintext == "***发言\n***発言"
    assert cues[1].plaintext == "***\n***"
    assert cues[0].start == 0 and cues[0].end == 1000
    assert original.read_bytes() == before
    assert runtime.artifacts.describe(filtered.subtitle_ref.artifact_id).test_only
    assert list(path.parent.glob("audit.json"))


def test_policy_update_blocks_previously_confirmed_subtitles(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    policy = runtime.settings.root / "config/subtitle-denylist.json"
    reviewer = SubtitleReviewer(runtime.artifacts, policy)
    service = PublicationService(
        runtime.factory, QualityGate(runtime.artifacts, reviewer.verify), clock, reviewer.prepare
    )
    prepared = service.prepare(manifest(assets, clock))
    service.confirm(prepared.id, prepared.manifest_hash)
    service.plan(prepared.id)
    rules, _ = load_rules(policy)
    rules.rules.append(DenyRule(id="new", aliases=["自产"]))
    policy.write_text(rules.model_dump_json(), encoding="utf-8")
    with pytest.raises(WorkflowError, match="仍命中"):
        service.plan(prepared.id)


def test_entirely_masked_cue_is_preserved(runtime: Runtime, assets: Assets, clock: Clock) -> None:
    path = runtime.artifacts.root / "empty.srt"
    path.write_text("1\n00:00:00,000 --> 00:00:01,900\n法轮功\n", encoding="utf-8")
    value = manifest(assets, clock)
    value.subtitle_ref = runtime.artifacts.register(path, "subtitle", test_only=True)
    reviewer = SubtitleReviewer(
        runtime.artifacts, runtime.settings.root / "config/subtitle-denylist.json"
    )
    result = reviewer.prepare(value)
    assert result.subtitle_ref
    cues = pysubs2.load(str(runtime.artifacts.resolve(result.subtitle_ref, "subtitle")))
    assert len(cues) == 1 and cues[0].plaintext == "***"
    assert cues[0].start == 0 and cues[0].end == 1900


def test_expanded_rules_cover_both_languages_without_blanket_country_mask(runtime: Runtime) -> None:
    rules, _ = load_rules(runtime.settings.root / "config/subtitle-denylist.json")
    result, _ = WordFilter(rules).clean_cues(
        [
            "高市总理谈自民党\n高市総理は自民党について",
            "性关系与贞操权\n性的な関係と貞操権",
            "日本便利店便当价格500日元",
        ]
    )
    assert result[0] == "****谈***\n****は***について"
    assert result[1] == "***与***\n*****と***"
    assert result[2] == "日本便利店便当价格500日元"


def test_latest_rules_checked_before_subtitle_write(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    prepared = runtime.publications.prepare(manifest(assets, clock))
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, prepared.id)
        row.remote_id, row.status = "BV1234567890", "uploaded"
    policy = runtime.settings.root / "config/subtitle-denylist.json"
    rules, _ = load_rules(policy)
    rules.rules.append(DenyRule(id="new", aliases=["自产"]))
    policy.write_text(rules.model_dump_json(), encoding="utf-8")
    live = replace(runtime, settings=replace(runtime.settings, publication_enabled=True))
    gateway = FakeGateway()
    with pytest.raises(WorkflowError, match="仍命中"):
        PublicationExecutor(live, gateway, legacy_native_subtitles=True).attach_subtitles(
            prepared.id
        )
    assert gateway.writes == 0
    with runtime.factory() as db:
        assert runtime.publications.require(db, prepared.id).status == "uploaded"
