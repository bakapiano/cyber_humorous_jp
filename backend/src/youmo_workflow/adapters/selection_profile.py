import json
import re
from datetime import datetime
from pathlib import Path

from youmo_workflow.adapters.selection_files import (
    checked_bytes,
    durable_path,
    read_lines,
    read_model,
    sha256,
)
from youmo_workflow.adapters.selection_history import HistoryInputs, retrieve_examples
from youmo_workflow.adapters.selection_models import (
    CurrentEntry,
    DatasetManifest,
    FrozenInitialCall,
    HistoricalGroup,
    HistoricalSource,
    ModelSettings,
    Pilot,
    ResearchConfig,
    TrainingSplit,
)
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.model_policy import DEFAULT_REASONING_EFFORT
from youmo_workflow.domain.selection import InitialRequest, ModelCandidate, ModelExample
from youmo_workflow.domain.time import BUSINESS_ZONE

V004_PROMPT_SHA256 = "20a40f9b78b5202288a2d65ee68115e066264b8d647f6e4b6dc569e77ee5cebe"


def duration_iso(seconds: int | None) -> str:
    if seconds is None:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return (
        "PT"
        + (f"{hours}H" if hours else "")
        + (f"{minutes}M" if minutes else "")
        + (f"{seconds}S" if seconds or not remainder and not hours else "")
    )


class V004Profile:
    def __init__(self, source_root: Path) -> None:
        self.root = source_root.resolve()

    def freeze(self, request: InitialRequest) -> FrozenInitialCall:
        if len(request.items) > 2000:
            raise WorkflowError(
                "SELECTION_INPUT_TOO_LARGE", "本次候选超过 2000 条保护上限，未截断或调用模型。"
            )
        entry = read_model(self.root / "首轮选题_当前方案.json", CurrentEntry)
        if entry.prompt_version != "v004":
            raise WorkflowError(
                "SELECTION_VERSION_CHANGED",
                "旧 v004 任务未冻结输入，请从新版入口显式启动；不会暗中升级或读临时归档。",
            )
        prompt_path = durable_path(entry.prompt_template, self.root)
        config_path = durable_path(entry.config_path, self.root)
        config = read_model(config_path, ResearchConfig)
        template = checked_bytes(prompt_path, prompt_path.parent)
        if sha256(template) != V004_PROMPT_SHA256:
            raise WorkflowError(
                "SELECTION_VERSION_CHANGED", "当前入口不再是已接入的 v004 prompt，请显式升级。"
            )
        if config.settings.thinking != {"type": "enabled"} or config.settings.response_format != {
            "type": "json_object"
        }:
            raise WorkflowError("SELECTION_SETTINGS_CHANGED", "当前模型设置与冻结方案不一致。")
        return self.build_call(request, entry, config.settings, template)

    def build_call(
        self, request: InitialRequest, entry: CurrentEntry, settings: ModelSettings, template: bytes
    ) -> FrozenInitialCall:
        settings = settings.model_copy(update={"reasoning_effort": DEFAULT_REASONING_EFFORT})
        history, hashes = self.history(entry, request)
        candidates = [
            ModelCandidate(
                candidate_id=f"C{index:03}",
                title=row.title,
                channel=row.channel_title or row.source_id,
                duration_iso=duration_iso(row.duration_seconds),
            )
            for index, row in enumerate(request.items, 1)
        ]
        day = datetime.fromisoformat(request.snapshot_at).astimezone(BUSINESS_ZONE).date()
        examples, trace = retrieve_examples(day, candidates, history)
        if candidates and not examples:
            raise WorkflowError(
                "SELECTION_HISTORY_EMPTY", "没有符合训练分区与时间边界的历史原标题样例。"
            )
        prompt = render_prompt(
            template.decode("utf-8-sig").replace("\r\n", "\n"), candidates, examples
        )
        hashes.update(
            {
                "prompt_template": sha256(template),
                "settings": sha256(durable_path(entry.config_path, self.root).read_bytes()),
                "effective_model_settings": sha256(settings.model_dump_json().encode()),
            }
        )
        return FrozenInitialCall(
            request=request,
            request_sha256=sha256(request.model_dump_json().encode()),
            settings=settings,
            rank_count=min(entry.rank_count, len(candidates)),
            candidates=candidates,
            examples=examples,
            example_trace=trace,
            source_hashes=hashes,
            prompt=prompt,
            prompt_sha256=sha256(prompt.encode()),
        )

    def history(
        self, entry: CurrentEntry, request: InitialRequest
    ) -> tuple[HistoryInputs, dict[str, str]]:
        dataset = durable_path(entry.dataset_path, self.root)
        manifest = read_model(dataset / "manifest.json", DatasetManifest)
        base = durable_path(manifest.base_dataset, self.root)
        if sha256((base / "file_manifest.json").read_bytes()) != manifest.base_manifest_sha256:
            raise WorkflowError("SELECTION_DATASET_CHANGED", "历史基础数据清单已变化。")
        sources_path, groups_path = base / "已识别来源原片.jsonl", base / "来源关联组.jsonl"
        source_bytes, group_bytes = (
            checked_bytes(sources_path, base),
            checked_bytes(groups_path, base),
        )
        training = read_model(dataset / "train.json", TrainingSplit)
        pilot = read_model(dataset / "train_pilot_12days.json", Pilot)
        sources = read_lines(sources_path, HistoricalSource)
        groups = read_lines(groups_path, HistoricalGroup)
        train_groups, train_sources = set(training.group_ids), set(training.source_ids)
        current_ids = {row.video_id for row in request.items}
        excluded = {group for day in pilot.days for group in day.known_group_ids}
        excluded.update(row.group_id for row in sources if row.video_id in current_ids)
        history = HistoryInputs(
            [
                row
                for row in sources
                if row.group_id in train_groups and row.video_id in train_sources
            ],
            {row.group_id: row for row in groups},
            excluded,
        )
        hashes = {
            "history_sources": sha256(source_bytes),
            "history_groups": sha256(group_bytes),
            "training_split": sha256((dataset / "train.json").read_bytes()),
            "pilot_exclusions": sha256((dataset / "train_pilot_12days.json").read_bytes()),
        }
        return history, hashes


def render_prompt(
    template: str, candidates: list[ModelCandidate], examples: list[ModelExample]
) -> str:
    values = {
        "candidate_count": str(len(candidates)),
        "rank_count": str(min(50, len(candidates))),
        "candidates_json": json.dumps(
            [row.model_dump() for row in candidates], ensure_ascii=False, separators=(",", ":")
        ),
        "examples_json": json.dumps(
            [row.model_dump() for row in examples], ensure_ascii=False, separators=(",", ":")
        ),
    }
    # Replace placeholders only in the template, never reinterpret untrusted titles as templates.
    rendered = re.sub(
        r"\{\{(candidate_count|rank_count|candidates_json|examples_json)\}\}",
        lambda match: values[match.group(1)],
        template,
    )
    if len(rendered) > 500000:
        raise WorkflowError("SELECTION_INPUT_TOO_LARGE", "模型输入超过保护上限，未删改候选。")
    return rendered
