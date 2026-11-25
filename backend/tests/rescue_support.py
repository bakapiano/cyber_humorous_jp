from pathlib import Path

import httpx
from selection_support import completion, frozen_call

from youmo_workflow.adapters.rescue_profile import V008Profile
from youmo_workflow.adapters.rescue_selection import V008InitialSelector
from youmo_workflow.adapters.selection_completion import DeepSeekCompletion
from youmo_workflow.adapters.selection_journal import SelectionJournal
from youmo_workflow.adapters.selection_models import FrozenInitialCall, SavedCompletion
from youmo_workflow.domain.selection import InitialRequest, ModelRanking


class FixtureRescueProfile(V008Profile):
    def freeze(self, request: InitialRequest) -> FrozenInitialCall:
        return frozen_call(request).model_copy(
            update={
                "version": "v008",
                "rescue_template": "RESCUE {{candidate_count}} {{anchors_json}} {{candidates_json}} {{examples_json}}",
            }
        )


def rescue_completion(call: FrozenInitialCall) -> SavedCompletion:
    baseline = completion(call)
    assert baseline.content is not None
    rows = ModelRanking.model_validate_json(baseline.content).ranked_candidates
    extra = [
        rows[0].model_copy(update={"candidate_id": row.candidate_id})
        for row in call.candidates[50:60]
    ]
    return baseline.model_copy(
        update={"content": ModelRanking(ranked_candidates=rows[30:50] + extra).model_dump_json()}
    )


def provider(root: Path, client: httpx.Client) -> V008InitialSelector:
    return V008InitialSelector(
        FixtureRescueProfile(root),
        (SelectionJournal(root / "v008/baseline"), SelectionJournal(root / "v008/rescue")),
        DeepSeekCompletion(client, root / "unused"),
    )


def http_result(result: SavedCompletion) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": result.model,
            "choices": [
                {
                    "finish_reason": result.finish_reason,
                    "message": {"content": result.content, "reasoning_content": "not-retained"},
                }
            ],
        },
    )
