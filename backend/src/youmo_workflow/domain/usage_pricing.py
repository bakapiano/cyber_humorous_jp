from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from youmo_workflow.domain.models import Contract
from youmo_workflow.domain.time import BUSINESS_ZONE
from youmo_workflow.domain.usage import TokenUsage, UsagePriceInfo, UsagePriceRate


class TokenRates(Contract):
    cache_hit: Decimal = Field(ge=0, decimal_places=3)
    cache_miss: Decimal = Field(ge=0, decimal_places=3)
    output: Decimal = Field(ge=0, decimal_places=3)

    def price(self, usage: TokenUsage) -> int | None:
        if usage.cache_hit is None or usage.cache_miss is None:
            return None
        return int(
            (
                usage.cache_hit * self.cache_hit
                + usage.cache_miss * self.cache_miss
                + usage.output * self.output
            )
            * 1000
        )


class ModelPrice(Contract):
    name: str
    aliases: list[str]
    off_peak: TokenRates
    peak: TokenRates


class UsagePricing(Contract):
    version: str
    verified_at: str
    source_url: str
    currency: Literal["CNY"]
    models: list[ModelPrice]
    holidays: dict[str, list[tuple[str, str]]]
    calendar_sources: list[str]

    def peak_period(self, at: datetime) -> bool | None:
        local = at.astimezone(BUSINESS_ZONE)
        if local.weekday() >= 5 or not (9 <= local.hour < 12 or 14 <= local.hour < 18):
            return False
        year, day = str(local.year), local.date().isoformat()
        if year not in self.holidays:
            return None
        if any(start <= day <= end for start, end in self.holidays[year]):
            return False
        return 9 <= local.hour < 12 or 14 <= local.hour < 18

    def price(self, model: str, at: datetime | None, usage: TokenUsage | None) -> int | None:
        if at is None or usage is None:
            return None
        peak = self.peak_period(at)
        match = next((entry for entry in self.models if model in entry.aliases), None)
        if peak is None or match is None:
            return None
        return (match.peak if peak else match.off_peak).price(usage)

    def info(self) -> UsagePriceInfo:
        return UsagePriceInfo(
            version=self.version,
            verified_at=self.verified_at,
            source_url=self.source_url,
            calendar_years=sorted(self.holidays),
            rates=[
                UsagePriceRate(
                    name=model.name,
                    period=period,
                    cache_hit=str(rate.cache_hit),
                    cache_miss=str(rate.cache_miss),
                    output=str(rate.output),
                )
                for model in self.models
                for period, rate in (("peak", model.peak), ("off_peak", model.off_peak))
            ],
        )
