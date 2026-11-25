from pydantic import ValidationError

from youmo_workflow.adapters.selection_completion import DeepSeekCompletion
from youmo_workflow.adapters.selection_files import evidence_matches
from youmo_workflow.adapters.selection_journal import SelectionJournal
from youmo_workflow.adapters.selection_models import FrozenInitialCall, SavedCompletion
from youmo_workflow.adapters.selection_profile import V004Profile
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.selection import (
    InitialOutput,
    InitialRequest,
    ModelRanking,
    RankedCandidate,
)


class V004InitialSelector:
    def __init__(
        self, profile: V004Profile, journal: SelectionJournal, model: DeepSeekCompletion
    ) -> None:
        self.profile, self.journal, self.model = profile, journal, model

    def rank(self, request: InitialRequest) -> InitialOutput:
        if not request.items:
            raise WorkflowError("INITIAL_INPUT_EMPTY", "没有初筛候选，未调用模型。")
        call = self.journal.freeze(request, self.profile.freeze)
        result = self.journal.cached(call)
        if result is None:
            key = self.model.credential()
            resume = (
                (lambda: self.model.complete(call, key, True)) if self.model.retry_policy else None
            )
            result = self.journal.execute(call, lambda: self.model.complete(call, key), resume)
        result = self.model.checked(
            call,
            result,
            lambda item: validate_ranking(call, item),
            self.journal.folder(request.batch_id),
        )
        return validate_ranking(call, result)


def validate_ranking(call: FrozenInitialCall, result: SavedCompletion) -> InitialOutput:
    if result.finish_reason != "stop" or result.model != call.settings.model or not result.content:
        raise WorkflowError(
            "INITIAL_SELECTION_INCOMPLETE", "模型输出被截断或模型版本不匹配，未把部分结果当成成功。"
        )
    try:
        parsed = ModelRanking.model_validate_json(result.content)
    except ValidationError as error:
        raise WorkflowError(
            "INITIAL_SELECTION_INVALID",
            "模型排序不符合初筛 JSON 输出契约："
            + error.json(include_input=False, include_url=False),
        ) from None
    bindings = {
        candidate.candidate_id: item
        for candidate, item in zip(call.candidates, call.request.items, strict=True)
    }
    ids = [row.candidate_id for row in parsed.ranked_candidates]
    if len(ids) != call.rank_count or len(set(ids)) != len(ids) or not set(ids) <= bindings.keys():
        raise WorkflowError("INITIAL_SELECTION_INVALID", "模型排序条数、重复项或来源 ID 无效。")
    ranked = [
        RankedCandidate(
            **row.model_dump(),
            rank=index,
            video=bindings[row.candidate_id],
            evidence_matches_title=evidence_matches(row.evidence, bindings[row.candidate_id].title),
        )
        for index, row in enumerate(parsed.ranked_candidates, 1)
    ]
    shortlist = ranked[: call.shortlist_limit]
    if any(not row.evidence_matches_title for row in shortlist):
        raise WorkflowError(
            "INITIAL_SELECTION_EVIDENCE_INVALID", "入围结果的引用不是原标题连续原文，未伪装成通过。"
        )
    selected_ids = [row.video.video_id for row in shortlist]
    return InitialOutput(
        batch_id=call.request.batch_id,
        version=call.version,
        input_sha256=call.request_sha256,
        prompt_sha256=call.prompt_sha256,
        rank_count=call.rank_count,
        shortlist_limit=call.shortlist_limit,
        ranked=ranked,
        shortlisted_ids=selected_ids,
        not_shortlisted_ids=[
            item.video_id for item in call.request.items if item.video_id not in selected_ids
        ],
    )
