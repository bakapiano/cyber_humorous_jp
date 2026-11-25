from typing import Literal

from pydantic import BaseModel, Field

from youmo_workflow.domain.model_policy import DEFAULT_REASONING_EFFORT, ReasoningEffort
from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.selection import (
    InitialRequest,
    ModelCandidate,
    ModelExample,
    SelectionVersion,
)


class CurrentEntry(BaseModel):
    prompt_version: SelectionVersion
    prompt_template: str
    config_path: str
    dataset_path: str
    rank_count: Literal[50]
    daily_shortlist_k: Literal[30]
    pipeline_definition: str | None = None


class ModelSettings(Contract):
    model: Literal["deepseek-flash"] = "deepseek-flash"
    thinking: dict[str, str] = Field(default_factory=lambda: {"type": "enabled"})
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT
    max_tokens: Literal[131072] = 131072
    stream: Literal[False] = False
    response_format: dict[str, str] = Field(default_factory=lambda: {"type": "json_object"})


class ResearchConfig(BaseModel):
    prompt_version: Literal["v004"]
    settings: ModelSettings
    rank_count: Literal[50]
    default_shortlist_k: Literal[30]
    candidate_description_enabled: Literal[False]
    rich_examples_enabled: Literal[False]


class ManifestFile(BaseModel):
    path: str
    sha256: str


class FileManifest(BaseModel):
    files: list[ManifestFile] = Field(default_factory=list[ManifestFile])
    hashes_sha256: dict[str, str] = Field(default_factory=dict)


class DatasetManifest(BaseModel):
    base_dataset: str
    base_manifest_sha256: str


class TrainingSplit(BaseModel):
    group_ids: list[str]
    source_ids: list[str]


class PilotDay(BaseModel):
    known_group_ids: list[str]


class Pilot(BaseModel):
    days: list[PilotDay]


class HistoricalPost(BaseModel):
    bvid: str
    published: str


class HistoricalSource(BaseModel):
    video_id: str
    group_id: str
    title: str
    published: str
    bilibili_references: list[HistoricalPost]


class HistoricalGroup(BaseModel):
    group_id: str
    last_post_date: str


class ExampleTrace(Contract):
    example_id: str
    video_id: str
    group_id: str
    published: str
    group_last_post_date: str
    bilibili_published: str


class FrozenInitialCall(Contract):
    request: InitialRequest
    request_sha256: str
    version: SelectionVersion = "v004"
    settings: ModelSettings
    rank_count: int
    shortlist_limit: Literal[30] = 30
    candidates: list[ModelCandidate]
    examples: list[ModelExample]
    example_trace: list[ExampleTrace]
    source_hashes: dict[str, str]
    prompt: str
    prompt_sha256: str
    rescue_template: str | None = None


class CompletionMessage(BaseModel):
    content: str | None = None


class CompletionChoice(BaseModel):
    finish_reason: str
    message: CompletionMessage


class CompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class CompletionResponse(BaseModel):
    model: str
    choices: list[CompletionChoice]
    usage: CompletionUsage = Field(default_factory=CompletionUsage)


class SavedCompletion(Contract):
    request_sha256: str
    model: str
    content: str | None
    finish_reason: str
    usage: CompletionUsage
