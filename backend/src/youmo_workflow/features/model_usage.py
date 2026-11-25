from datetime import datetime

from youmo_workflow.domain.time import stamp
from youmo_workflow.domain.usage import UsageEvent, UsageReport, UsageRow, UsageStage, UsageTotals
from youmo_workflow.domain.usage_pricing import UsagePricing
from youmo_workflow.features.usage_attribution import UsageContext
from youmo_workflow.features.usage_totals import add_totals, allocated, event_totals, incomplete


def build_usage_report(
    events: list[UsageEvent],
    context: UsageContext,
    pricing: UsagePricing,
    at: datetime,
    duplicates: int = 0,
) -> UsageReport:
    videos: dict[str, UsageRow] = {}
    workflows: dict[str, UsageRow] = {}
    stages: dict[str, UsageTotals] = {}
    summary, unattributed = UsageTotals(), UsageTotals()
    for event in events:
        totals = event_totals(event, pricing)
        if incomplete(totals):
            continue
        add_totals(summary, totals)
        add_totals(stages.setdefault(event.stage, UsageTotals()), totals)
        video_id, batch_id = (
            context.content(event.content_id) if event.content_id else (None, event.batch_id)
        )
        if video_id:
            video = video_row(videos, video_id, context)
            append_event(video, event, totals, shared=False)
        if batch_id:
            workflow = workflow_row(workflows, batch_id, context)
            append_event(workflow, event, totals, shared=video_id is None)
        else:
            add_totals(unattributed, totals)
    allocate_selection(videos, workflows, context)
    for row in (*videos.values(), *workflows.values()):
        add_totals(row.total, row.direct)
        add_totals(row.total, row.shared)
    return UsageReport(
        captured_at=stamp(at),
        pricing=pricing.info(),
        summary=summary,
        videos=sorted(videos.values(), key=lambda row: row.total.cost_nano_cny, reverse=True),
        workflows=sorted(workflows.values(), key=lambda row: row.last_call_at or "", reverse=True),
        stages=[UsageStage(stage=stage, totals=totals) for stage, totals in sorted(stages.items())],
        unattributed_workflow=unattributed,
        duplicates_removed=duplicates,
    )


def video_row(rows: dict[str, UsageRow], identifier: str, context: UsageContext) -> UsageRow:
    if identifier not in rows:
        rows[identifier] = UsageRow(
            id=identifier,
            title=context.video_titles.get(identifier, identifier),
            video_count=1,
            batch_ids=sorted(context.video_batches.get(identifier, set())),
            delivered_count=int(identifier in context.delivered_videos),
        )
    return rows[identifier]


def workflow_row(rows: dict[str, UsageRow], identifier: str, context: UsageContext) -> UsageRow:
    if identifier not in rows:
        record = context.workflows.get(identifier)
        rows[identifier] = UsageRow(
            id=identifier,
            title=record.title if record else identifier,
            status=record.status if record else None,
            batch_ids=[identifier],
            video_count=len(record.video_ids) if record else 0,
            delivered_count=len(record.delivered) if record else 0,
        )
    return rows[identifier]


def append_event(row: UsageRow, event: UsageEvent, totals: UsageTotals, *, shared: bool) -> None:
    add_totals(row.shared if shared else row.direct, totals)
    stage = next((stage for stage in row.stages if stage.stage == event.stage), None)
    if stage is None:
        stage = UsageStage(stage=event.stage, totals=UsageTotals())
        row.stages.append(stage)
    add_totals(stage.totals, totals)
    if event.at:
        row.last_call_at = max(row.last_call_at or "", stamp(event.at))


def allocate_selection(
    videos: dict[str, UsageRow], workflows: dict[str, UsageRow], context: UsageContext
) -> None:
    for identifier, workflow in workflows.items():
        record = context.workflows.get(identifier)
        recipients = sorted(record.video_ids) if record else []
        for index, video_id in enumerate(recipients):
            video = video_row(videos, video_id, context)
            add_totals(video.shared, allocated(workflow.shared, index, len(recipients)))
