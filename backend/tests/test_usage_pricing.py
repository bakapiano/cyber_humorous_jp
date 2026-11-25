from datetime import datetime

import pytest
from usage_support import prices

from youmo_workflow.domain.usage import TokenUsage, UsageEvent
from youmo_workflow.features.model_usage import build_usage_report
from youmo_workflow.features.usage_attribution import UsageContext, WorkflowUsageContext


@pytest.mark.parametrize(
    "local,peak",
    [
        ("2026-11-06T08:59:59+08:00", False),
        ("2026-11-06T09:00:00+08:00", True),
        ("2026-11-06T11:59:59+08:00", True),
        ("2026-11-06T12:00:00+08:00", False),
        ("2026-11-06T14:00:00+08:00", True),
        ("2026-11-06T17:59:59+08:00", True),
        ("2026-11-06T18:00:00+08:00", False),
        ("2026-11-07T10:00:00+08:00", False),
        ("2026-10-01T10:00:00+08:00", False),
        ("2026-10-10T10:00:00+08:00", False),
        ("2028-01-03T10:00:00+08:00", None),
        ("2028-01-03T20:00:00+08:00", False),
    ],
)
def test_official_peak_boundaries_weekends_and_holidays(local: str, peak: bool | None) -> None:
    assert prices().peak_period(datetime.fromisoformat(local)) is peak


def test_cost_uses_cache_split_and_output_once_including_reasoning() -> None:
    usage = TokenUsage(1_000_000, 1_000_000, 250_000, 750_000, 900_000)
    low, high = (
        datetime.fromisoformat(value)
        for value in ("2026-11-07T10:00:00+08:00", "2026-11-06T10:00:00+08:00")
    )
    assert prices().price("deepseek-flash", low, usage) == 4_755_000_000
    assert prices().price("deepseek-v4-flash", high, usage) == 9_510_000_000
    assert prices().price("unknown-model", low, usage) is None
    assert prices().price("deepseek-flash", None, usage) is None
    assert prices().price("deepseek-flash", low, TokenUsage(100, 200, None, None)) is None


def test_video_and_workflow_costs_reconcile_without_double_counting_shared_calls() -> None:
    at = datetime.fromisoformat("2026-11-07T10:00:00+08:00")
    context = UsageContext(
        video_titles={"v1": "one", "v2": "two"},
        workflows={
            "batch": WorkflowUsageContext("batch", "running", {"v1", "v2"}),
            "failed": WorkflowUsageContext("failed", "failed"),
        },
    )
    context.link("content1", "v1", "batch")
    context.link("content2", "v2", "batch")
    events = [
        UsageEvent("s", "deepseek-flash", at, TokenUsage(5, 7, 2, 3, 4), None, "batch", "初筛"),
        UsageEvent("v1", "deepseek-flash", at, TokenUsage(8, 9, 1, 7), "content1", None, "翻译"),
        UsageEvent(
            "v2", "deepseek-flash", at, TokenUsage(4, 10, 0, 4), "content2", None, "翻译", True
        ),
        UsageEvent("u", "deepseek-flash", at, None, "content2", None, "翻译", True),
        UsageEvent("f", "deepseek-flash", at, TokenUsage(3, 2, 0, 3), None, "failed", "初筛"),
    ]
    report = build_usage_report(events, context, prices(), at)
    batch = next(row for row in report.workflows if row.id == "batch")
    assert report.summary.calls == 4 and report.summary.retries == 1
    assert report.summary.total_tokens == 48 and report.summary.unmetered_calls == 0
    assert sum(row.total.cost_nano_cny for row in report.workflows) == report.summary.cost_nano_cny
    assert sum(row.shared.cost_nano_cny for row in report.videos) == batch.shared.cost_nano_cny
    assert sum(row.total.cost_nano_cny for row in report.videos) == batch.total.cost_nano_cny
    for row in report.videos:
        assert row.shared.input_tokens == row.shared.cache_hit_tokens + row.shared.cache_miss_tokens
    assert not any(row.incomplete for row in report.videos)
    assert report.unattributed_workflow.calls == 0
    assert report.warnings == []


@pytest.mark.parametrize("missing", ["model", "usage", "time", "cache", "calendar"])
def test_incomplete_calls_are_excluded_from_all_report_dimensions(missing: str) -> None:
    at = datetime.fromisoformat("2026-11-07T10:00:00+08:00")
    event = UsageEvent(
        "x",
        "unknown" if missing == "model" else "deepseek-flash",
        None
        if missing == "time"
        else (datetime.fromisoformat("2028-01-03T10:00:00+08:00") if missing == "calendar" else at),
        None
        if missing == "usage"
        else TokenUsage(
            20, 10, None if missing == "cache" else 0, None if missing == "cache" else 20
        ),
        "local-preview",
        "batch",
        "preview",
        True,
    )
    report = build_usage_report([event], UsageContext(), prices(), at)
    assert all(value == 0 for value in report.summary.model_dump().values())
    assert report.videos == report.workflows == report.stages == report.warnings == []
    assert report.unattributed_workflow.calls == 0


def test_complete_unattributed_and_zero_cost_calls_are_kept_without_warnings() -> None:
    at = datetime.fromisoformat("2026-11-07T10:00:00+08:00")
    events = [
        UsageEvent("one", "deepseek-flash", at, TokenUsage(20, 10, 0, 20), None, None, "trial"),
        UsageEvent("zero", "deepseek-flash", at, TokenUsage(0, 0, 0, 0), None, None, "trial"),
    ]
    report = build_usage_report(events, UsageContext(), prices(), at)
    assert report.summary.calls == report.summary.metered_calls == 2
    assert report.summary.total_tokens == 30
    assert report.unattributed_workflow == report.summary
    assert report.warnings == []


def test_incomplete_selection_does_not_spread_partial_flags_or_cost_to_videos() -> None:
    at = datetime.fromisoformat("2026-11-07T10:00:00+08:00")
    context = UsageContext(workflows={"batch": WorkflowUsageContext("batch", "running", {"v1"})})
    context.link("content1", "v1", "batch")
    events = [
        UsageEvent("s", "deepseek-flash", at, None, None, "batch", "初筛", True),
        UsageEvent("v", "deepseek-flash", at, TokenUsage(8, 9, 1, 7), "content1", None, "翻译"),
    ]
    report = build_usage_report(events, context, prices(), at)
    assert report.summary.calls == 1 and report.summary.retries == 0
    assert report.videos[0].total == report.videos[0].direct
    assert not report.videos[0].incomplete and not report.videos[0].shared_incomplete
    assert [stage.stage for stage in report.stages] == ["翻译"]
