from dataclasses import dataclass
from datetime import datetime

from pydantic import ConfigDict, Field

from youmo_workflow.domain.models import Contract


@dataclass(frozen=True)
class TokenUsage:
    input: int
    output: int
    cache_hit: int | None
    cache_miss: int | None
    reasoning: int = 0


@dataclass(frozen=True)
class UsageEvent:
    identity: str
    model: str
    at: datetime | None
    usage: TokenUsage | None
    content_id: str | None
    batch_id: str | None
    stage: str
    retry: bool = False


class UsageContract(Contract):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class UsageTotals(UsageContract):
    calls: int = 0
    retries: int = 0
    metered_calls: int = 0
    unmetered_calls: int = 0
    unpriced_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    cache_unknown_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_nano_cny: int = 0


class UsageStage(UsageContract):
    stage: str
    totals: UsageTotals


class UsageRow(UsageContract):
    id: str
    title: str
    batch_ids: list[str] = Field(default_factory=list[str])
    status: str | None = None
    video_count: int = 0
    delivered_count: int = 0
    last_call_at: str | None = None
    direct: UsageTotals = Field(default_factory=UsageTotals)
    shared: UsageTotals = Field(default_factory=UsageTotals)
    total: UsageTotals = Field(default_factory=UsageTotals)
    incomplete: bool = False
    shared_incomplete: bool = False
    stages: list[UsageStage] = Field(default_factory=list[UsageStage])


class UsagePriceRate(UsageContract):
    name: str
    cache_hit: str
    cache_miss: str
    output: str
    period: str


class UsagePriceInfo(UsageContract):
    version: str
    verified_at: str
    source_url: str
    currency: str = "CNY"
    rates: list[UsagePriceRate]
    calendar_years: list[str]


class UsageReport(UsageContract):
    captured_at: str
    pricing: UsagePriceInfo
    summary: UsageTotals
    videos: list[UsageRow]
    workflows: list[UsageRow]
    stages: list[UsageStage]
    unattributed_workflow: UsageTotals
    duplicates_removed: int = 0
    warnings: list[str] = Field(default_factory=list[str])
