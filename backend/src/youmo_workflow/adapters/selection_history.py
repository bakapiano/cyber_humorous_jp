import math
import random
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from youmo_workflow.adapters.selection_models import ExampleTrace, HistoricalGroup, HistoricalSource
from youmo_workflow.domain.selection import ModelCandidate, ModelExample


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"tbs\s*news\s*dig|日テレnews|annnews|nnnセレクション|#shorts", "", value)
    value = re.sub(r"20\d\d年\d+月\d+日|\d+", "", value)
    return re.sub(r"[\W_]+", "", value)


def grams(value: str) -> set[str]:
    value = normalize_title(value)
    return {value[index : index + 3] for index in range(max(1, len(value) - 2))}


@dataclass
class HistoryInputs:
    sources: list[HistoricalSource]
    groups: dict[str, HistoricalGroup]
    excluded_groups: set[str]


@dataclass
class MatchedExample:
    source: HistoricalSource
    candidate_id: str
    similarity: float


def rank_examples(
    candidates: list[ModelCandidate], sources: list[HistoricalSource]
) -> list[MatchedExample]:
    candidate_grams = [(row.candidate_id, grams(row.title)) for row in candidates]
    result: list[MatchedExample] = []
    for source in sources:
        source_grams = grams(source.title)
        candidate, score = max(
            (
                (cid, len(source_grams & item) / math.sqrt(max(1, len(source_grams) * len(item))))
                for cid, item in candidate_grams
            ),
            key=lambda row: (row[1], row[0]),
        )
        result.append(MatchedExample(source, candidate, score))
    return sorted(result, key=lambda row: (-row.similarity, row.source.video_id))


def diversify(ranked: list[MatchedExample], day: date) -> list[MatchedExample]:
    selected: list[MatchedExample] = []
    used: set[str] = set()
    counts: Counter[str] = Counter()
    for item in ranked:
        if item.source.group_id in used or counts[item.candidate_id] >= 2:
            continue
        selected.append(item)
        used.add(item.source.group_id)
        counts[item.candidate_id] += 1
        if len(selected) == 24:
            break
    remaining = [row for row in ranked if row.source.group_id not in used]
    random.Random(20261106 + int(day.strftime("%Y%m%d"))).shuffle(remaining)
    for item in remaining:
        if item.source.group_id in used:
            continue
        selected.append(item)
        used.add(item.source.group_id)
        if len(selected) == 32:
            break
    return selected


def retrieve_examples(
    day: date, candidates: list[ModelCandidate], history: HistoryInputs
) -> tuple[list[ModelExample], list[ExampleTrace]]:
    if not candidates:
        return [], []
    cutoff = (day - timedelta(days=7)).isoformat()
    titles = {normalize_title(row.title) for row in candidates}
    available = [row for row in history.sources if eligible(row, history, cutoff, titles)]
    selected = diversify(rank_examples(candidates, available), day)
    visible: list[ModelExample] = []
    trace: list[ExampleTrace] = []
    for index, item in enumerate(selected, 1):
        row = item.source
        if len(row.bilibili_references) != 1:
            continue
        reference = row.bilibili_references[0]
        if reference.published[:10] >= cutoff:
            continue
        example_id = f"E{index:03}"
        visible.append(ModelExample(example_id=example_id, title=row.title))
        trace.append(
            ExampleTrace(
                example_id=example_id,
                video_id=row.video_id,
                group_id=row.group_id,
                published=row.published,
                group_last_post_date=history.groups[row.group_id].last_post_date,
                bilibili_published=reference.published,
            )
        )
    return visible, trace


def eligible(row: HistoricalSource, history: HistoryInputs, cutoff: str, titles: set[str]) -> bool:
    group = history.groups.get(row.group_id)
    return bool(
        group
        and row.group_id not in history.excluded_groups
        and group.last_post_date < cutoff
        and row.published[:10] < cutoff
        and normalize_title(row.title) not in titles
    )
