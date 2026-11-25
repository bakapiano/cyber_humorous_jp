import json
import re

from pydantic import ValidationError

from youmo_workflow.adapters.initial_selection import validate_ranking
from youmo_workflow.adapters.selection_files import evidence_matches, sha256
from youmo_workflow.adapters.selection_models import FrozenInitialCall, SavedCompletion
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.selection import InitialOutput, ModelRanking, RankingRow


def response_hash(response: SavedCompletion) -> str:
    return sha256(response.model_dump_json().encode())


def checked_rows(response: SavedCompletion, allowed: set[str], count: int) -> list[RankingRow]:
    if (
        response.finish_reason != "stop"
        or response.model != "deepseek-flash"
        or not response.content
    ):
        raise WorkflowError("INITIAL_SELECTION_INCOMPLETE", "排序响应未完整成功，未当作有效结果。")
    try:
        rows = ModelRanking.model_validate_json(response.content).ranked_candidates
    except ValidationError as error:
        raise WorkflowError(
            "INITIAL_SELECTION_INVALID",
            "排序响应不符合初筛 JSON 契约：" + error.json(include_input=False, include_url=False),
        ) from None
    ids = [row.candidate_id for row in rows]
    if len(ids) != count or len(set(ids)) != count or not set(ids) <= allowed:
        raise WorkflowError("INITIAL_SELECTION_INVALID", "补漏排序的数量、重复项或候选范围无效。")
    return rows


def validate_baseline(call: FrozenInitialCall, result: SavedCompletion) -> None:
    titles = {row.candidate_id: row.title for row in call.candidates}
    rows = checked_rows(result, set(titles), 50)
    if any(not evidence_matches(row.evidence, titles[row.candidate_id]) for row in rows[:20]):
        raise WorkflowError(
            "INITIAL_SELECTION_EVIDENCE_INVALID", "前20锚点引用不是原标题连续原文。"
        )


def rescue_call(base: FrozenInitialCall, baseline: SavedCompletion) -> FrozenInitialCall:
    if not base.rescue_template or base.version != "v008":
        raise WorkflowError("SELECTION_CONFIG_INVALID", "缺少 v008 冻结补漏模板。")
    visible = {row.candidate_id: row for row in base.candidates}
    rows = checked_rows(baseline, set(visible), 50)
    anchor_ids = {row.candidate_id for row in rows[:20]}
    anchors = [
        {**visible[row.candidate_id].model_dump(), "angle": row.angle, "verify": row.verify}
        for row in rows[:20]
    ]
    remaining = [row for row in base.candidates if row.candidate_id not in anchor_ids]
    values = {
        "candidate_count": str(len(remaining)),
        "rank_count": "50",
        "candidates_json": json.dumps(
            [row.model_dump() for row in remaining], ensure_ascii=False, separators=(",", ":")
        ),
        "examples_json": json.dumps(
            [row.model_dump() for row in base.examples], ensure_ascii=False, separators=(",", ":")
        ),
        "anchors_json": json.dumps(anchors, ensure_ascii=False, separators=(",", ":")),
    }
    prompt = re.sub(
        r"\{\{(candidate_count|rank_count|candidates_json|examples_json|anchors_json)\}\}",
        lambda match: values[match.group(1)],
        base.rescue_template,
    )
    if len(prompt) > 500000:
        raise WorkflowError("SELECTION_INPUT_TOO_LARGE", "补漏输入超过保护上限，未截断候选。")
    return base.model_copy(
        update={
            "prompt": prompt,
            "prompt_sha256": sha256(prompt.encode()),
            "rank_count": 30,
            "candidates": remaining,
            "source_hashes": {**base.source_hashes, "baseline_response": response_hash(baseline)},
        }
    )


def compose_output(
    base: FrozenInitialCall,
    baseline: SavedCompletion,
    rescue: SavedCompletion,
    rescue_prompt_hash: str,
) -> InitialOutput:
    candidates = {row.candidate_id for row in base.candidates}
    rows = checked_rows(baseline, candidates, 50)
    # Final title-evidence validation covers the actual Top30, including fallback.
    if rescue.model != base.settings.model:
        raise WorkflowError("INITIAL_SELECTION_INVALID", "补漏响应模型不匹配。")
    fallback = rescue.finish_reason == "content_filter"
    if not fallback:
        remaining = candidates - {row.candidate_id for row in rows[:20]}
        rows = rows[:20] + checked_rows(rescue, remaining, 30)
    composed = baseline.model_copy(
        update={"content": ModelRanking(ranked_candidates=rows).model_dump_json()}
    )
    result = validate_ranking(base, composed)
    return result.model_copy(
        update={
            "composition": "baseline_fallback" if fallback else "anchor_rescue",
            "fallback_reason": "provider_content_filter" if fallback else None,
            "component_hashes": {
                "baseline_response": response_hash(baseline),
                "rescue_response": response_hash(rescue),
                "rescue_prompt": rescue_prompt_hash,
            },
        }
    )
