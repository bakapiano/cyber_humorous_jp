from youmo_workflow.domain.usage import UsageEvent, UsageTotals
from youmo_workflow.domain.usage_pricing import UsagePricing

SHARED_FIELDS = (
    "output_tokens",
    "cache_hit_tokens",
    "cache_miss_tokens",
    "cache_unknown_tokens",
    "reasoning_tokens",
    "cost_nano_cny",
)


def event_totals(event: UsageEvent, pricing: UsagePricing) -> UsageTotals:
    result = UsageTotals(calls=1, retries=int(event.retry))
    if event.usage is None:
        result.unmetered_calls = 1
        return result
    usage = event.usage
    result.metered_calls = 1
    result.input_tokens, result.output_tokens = usage.input, usage.output
    result.total_tokens = usage.input + usage.output
    result.reasoning_tokens = usage.reasoning
    if usage.cache_hit is None or usage.cache_miss is None:
        result.cache_unknown_tokens = usage.input
    else:
        result.cache_hit_tokens, result.cache_miss_tokens = usage.cache_hit, usage.cache_miss
    cost = pricing.price(event.model, event.at, usage)
    if cost is None:
        result.unpriced_calls = 1
    else:
        result.cost_nano_cny = cost
    return result


def add_totals(target: UsageTotals, source: UsageTotals) -> None:
    for name in UsageTotals.model_fields:
        setattr(target, name, getattr(target, name) + getattr(source, name))


def allocated(source: UsageTotals, index: int, count: int) -> UsageTotals:
    result = UsageTotals()
    for name in SHARED_FIELDS:
        base, remainder = divmod(getattr(source, name), count)
        setattr(result, name, base + int(index < remainder))
    result.input_tokens = (
        result.cache_hit_tokens + result.cache_miss_tokens + result.cache_unknown_tokens
    )
    result.total_tokens = result.input_tokens + result.output_tokens
    return result


def incomplete(totals: UsageTotals) -> bool:
    return totals.unmetered_calls > 0 or totals.unpriced_calls > 0
