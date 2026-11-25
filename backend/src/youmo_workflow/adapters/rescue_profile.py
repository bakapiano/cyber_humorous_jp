from typing import Literal

from pydantic import BaseModel

from youmo_workflow.adapters.selection_files import checked_bytes, durable_path, read_model, sha256
from youmo_workflow.adapters.selection_models import CurrentEntry, FrozenInitialCall, ModelSettings
from youmo_workflow.adapters.selection_profile import V004_PROMPT_SHA256, V004Profile
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.selection import InitialRequest

V008_PROMPT_SHA256 = "0deb39af95cfc725b1cb695360621d513e209154c51c6d083466f3dcaa957aeb"
V008_PIPELINE_SHA256 = "fad440c9f3bf0872b9ac0a46386a33b798bffa24a97f9ee894c13e8c65415e65"


class RescueConfig(BaseModel):
    prompt_version: Literal["v008"]
    pipeline_mode: Literal["v004_then_rescue"]
    settings: ModelSettings
    anchor_count: Literal[20]
    rank_count: Literal[50]
    default_shortlist_k: Literal[30]
    pipeline_calls_per_day: Literal[2]


class PipelineDefinition(BaseModel):
    mode: Literal["v004_then_rescue"]
    anchor_count: Literal[20]
    rank_count: Literal[50]
    daily_shortlist_k: Literal[30]
    calls_per_day: Literal[2]
    baseline_prompt: Literal["components/v004_prompt_template.txt"]
    rescue_prompt: Literal["prompt_template.txt"]


class V008Profile(V004Profile):
    def freeze(self, request: InitialRequest) -> FrozenInitialCall:
        if not 50 <= len(request.items) <= 2000:
            raise WorkflowError(
                "SELECTION_POOL_SIZE",
                "v008 当前固定生成 50 名排序，需要 50—2000 条候选；未删改模板或调用模型。",
            )
        entry = read_model(self.root / "首轮选题_当前方案.json", CurrentEntry)
        if entry.prompt_version != "v008" or entry.pipeline_definition is None:
            raise WorkflowError(
                "SELECTION_VERSION_CHANGED", "当前入口不再是已接入的 v008，请显式升级。"
            )
        rescue_path = durable_path(entry.prompt_template, self.root)
        folder = rescue_path.parent
        pipeline_path = durable_path(entry.pipeline_definition, folder)
        pipeline = read_model(pipeline_path, PipelineDefinition)
        config = read_model(durable_path(entry.config_path, self.root), RescueConfig)
        baseline_path = durable_path(str(folder / pipeline.baseline_prompt), folder)
        baseline = checked_bytes(baseline_path, folder)
        rescue = checked_bytes(rescue_path, folder)
        definition = checked_bytes(pipeline_path, folder)
        if (sha256(baseline), sha256(rescue), sha256(definition)) != (
            V004_PROMPT_SHA256,
            V008_PROMPT_SHA256,
            V008_PIPELINE_SHA256,
        ):
            raise WorkflowError("SELECTION_INPUT_CHANGED", "v008 冻结模板或编排定义哈希不匹配。")
        if config.settings.thinking != {"type": "enabled"} or config.settings.response_format != {
            "type": "json_object"
        }:
            raise WorkflowError("SELECTION_SETTINGS_CHANGED", "模型参数与已核实的 v008 不一致。")
        call = self.build_call(request, entry, config.settings, baseline)
        return call.model_copy(
            update={
                "version": "v008",
                "rescue_template": rescue.decode("utf-8-sig").replace("\r\n", "\n"),
                "source_hashes": {
                    **call.source_hashes,
                    "rescue_template": sha256(rescue),
                    "pipeline": sha256(definition),
                },
            }
        )
