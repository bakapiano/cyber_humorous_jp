from youmo_workflow.adapters.initial_selection import validate_ranking
from youmo_workflow.adapters.selection_files import sha256
from youmo_workflow.adapters.selection_models import (
    CompletionUsage,
    FrozenInitialCall,
    ModelSettings,
    SavedCompletion,
)
from youmo_workflow.adapters.selection_profile import duration_iso
from youmo_workflow.domain.models import DiscoveryVideo, VideoDTO
from youmo_workflow.domain.selection import (
    InitialOutput,
    InitialRequest,
    ModelCandidate,
    ModelRanking,
    RankingRow,
)


def initial_request(count: int = 55) -> InitialRequest:
    return InitialRequest(
        batch_id="synthetic-batch",
        snapshot_at="2026-11-06T03:00:00Z",
        items=[
            VideoDTO(
                video_id=f"test{i:07}",
                source_id="tbsnewsdig",
                title=f"実験用ニュースの原文{i}",
                channel_title="Synthetic channel",
                duration_seconds=90,
                published_at="2026-11-06T01:00:00Z",
                discovered_at="2026-11-06T03:00:00Z",
                revision=1,
                screened=False,
            )
            for i in range(count)
        ],
    )


def frozen_call(request: InitialRequest) -> FrozenInitialCall:
    prompt = "Synthetic title-only prompt"
    return FrozenInitialCall(
        request=request,
        request_sha256=sha256(request.model_dump_json().encode()),
        settings=ModelSettings(),
        rank_count=min(50, len(request.items)),
        candidates=[
            ModelCandidate(
                candidate_id=f"C{i:03}",
                title=row.title,
                channel=row.channel_title or "",
                duration_iso=duration_iso(row.duration_seconds),
            )
            for i, row in enumerate(request.items, 1)
        ],
        examples=[],
        example_trace=[],
        source_hashes={},
        prompt=prompt,
        prompt_sha256=sha256(prompt.encode()),
    )


def completion(call: FrozenInitialCall) -> SavedCompletion:
    ranking = ModelRanking(
        ranked_candidates=[
            RankingRow(
                candidate_id=row.candidate_id,
                angle="测试中的待核实角度",
                evidence_field="title",
                evidence="実験用ニュース",
                verify="内容是否支持该线索",
            )
            for row in call.candidates[:50]
        ]
    )
    return SavedCompletion(
        request_sha256=call.request_sha256,
        model="deepseek-flash",
        content=ranking.model_dump_json(),
        finish_reason="stop",
        usage=CompletionUsage(prompt_tokens=10, completion_tokens=20),
    )


class FixtureInitial:
    def rank(self, request: InitialRequest) -> InitialOutput:
        call = frozen_call(request)
        return validate_ranking(call, completion(call))


def discovery_items(count: int = 55) -> list[DiscoveryVideo]:
    return [
        DiscoveryVideo.model_validate(
            row.model_dump(exclude={"source_id", "discovered_at", "screened", "revision"})
        )
        for row in initial_request(count).items
    ]
