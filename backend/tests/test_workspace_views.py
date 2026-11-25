import json
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from conftest import Assets, Clock
from fastapi.testclient import TestClient
from test_publications import manifest

from youmo_workflow.api import create_app
from youmo_workflow.domain.time import stamp
from youmo_workflow.domain.workspace_views import BatchPage, PublicationPage, TaskPage
from youmo_workflow.features.publication_activity import publication_activity
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Publication, Task


def seed_pages(runtime: Runtime, clock: Clock, assets: Assets) -> None:
    at = stamp(clock())
    with runtime.factory.begin() as db:
        for i in range(105):
            key = f"{i:03d}"
            value = manifest(assets, clock).model_copy(update={"content_id": key})
            db.add(
                Batch(
                    id=key,
                    local_date="2026-11-06",
                    snapshot_at=at,
                    status="queued",
                    candidate_count=50,
                    policy_json=runtime.policy.model_dump_json(),
                )
            )
            db.add(
                Publication(
                    id=key,
                    content_id=key,
                    manifest_json=value.model_dump_json(),
                    manifest_hash=key,
                    status="prepared",
                    created_at=at,
                )
            )
            db.add(
                Task(
                    id=key,
                    unique_key=key,
                    kind="initial_selection",
                    status="pending",
                    created_at=at,
                    updated_at=at,
                    next_run_at=at,
                )
            )


@pytest.mark.parametrize(
    "name,model",
    [
        ("batches", BatchPage),
        ("tasks", TaskPage),
        ("publications", PublicationPage),
    ],
)
def test_real_pagination_reaches_past_legacy_caps(
    runtime: Runtime,
    clock: Clock,
    assets: Assets,
    name: str,
    model: type[BatchPage] | type[TaskPage] | type[PublicationPage],
) -> None:
    seed_pages(runtime, clock, assets)
    with TestClient(create_app(runtime)) as client:
        path = f"/api/v1/workspace/{name}"
        first = model.model_validate_json(client.get(path).content)
        assert first.total == 105 and len(first.items) == 10 and first.items[0].id == "104"
        last = model.model_validate_json(client.get(path, params={"offset": 100}).content)
        assert len(last.items) == 5 and last.items[-1].id == "000"
        clamped = model.model_validate_json(client.get(path, params={"offset": 9999}).content)
        assert clamped == last
        assert client.get(path, params={"limit": 0}).status_code == 422
        assert client.get(path, params={"limit": 101}).status_code == 422
        assert client.get(path, params={"offset": -1}).status_code == 422


@dataclass
class Delivery:
    key: int
    at: datetime
    status: str = "uploaded"
    test_only: bool = False
    receipt: int | None = None


def add_delivery(runtime: Runtime, clock: Clock, assets: Assets, delivery: Delivery) -> None:
    key, delivered_at = delivery.key, delivery.at
    value = manifest(assets, clock).model_copy(
        update={"content_id": str(key), "test_only": delivery.test_only}
    )
    remote = f"BV{delivery.receipt if delivery.receipt is not None else key:010d}"
    with runtime.factory.begin() as db:
        db.add(
            Publication(
                id=str(key),
                content_id=str(key),
                manifest_json=value.model_dump_json(),
                manifest_hash="hash",
                confirmed_hash="hash",
                status=delivery.status,
                remote_id=remote,
                created_at=stamp(clock() - timedelta(days=40)),
            )
        )
        db.add(
            Task(
                id=str(key),
                unique_key=str(key),
                kind="publish",
                status="succeeded",
                input_json=json.dumps({"publication_id": str(key)}),
                output_json=json.dumps({"remote_id": remote}),
                created_at=stamp(delivered_at - timedelta(minutes=1)),
                updated_at=stamp(delivered_at),
                next_run_at=stamp(delivered_at),
            )
        )


def test_activity_counts_receipt_time_deduplicates_and_excludes_unknown_tests_future(
    runtime: Runtime,
    clock: Clock,
    assets: Assets,
) -> None:
    at = clock()
    cases = [
        Delivery(1, at - timedelta(hours=1)),
        Delivery(2, at - timedelta(hours=24)),
        Delivery(3, at - timedelta(hours=24, seconds=1)),
        Delivery(4, at + timedelta(minutes=1)),
        Delivery(5, at, test_only=True),
        Delivery(6, at, status="unknown"),
        Delivery(7, at - timedelta(minutes=30), receipt=1),
        Delivery(8, at - timedelta(hours=2), status="withdrawn"),
    ]
    for delivery in cases:
        add_delivery(runtime, clock, assets, delivery)
    result = publication_activity(runtime.factory, 24, at)
    assert result.total == 3 and sum(row.count for row in result.buckets) == 3
    assert len(result.buckets) == 25 and result.interval == "hour"
    assert result.buckets[-1].label == "11-06 11:00"
    assert publication_activity(runtime.factory, 168, at).total == 4


def test_activity_uses_shanghai_midnight_and_all_records_not_current_page(
    runtime: Runtime,
    clock: Clock,
    assets: Assets,
) -> None:
    at = clock().replace(hour=17)
    for i in range(105):
        add_delivery(runtime, clock, assets, Delivery(i, at - timedelta(hours=1 if i < 50 else 2)))
    result = publication_activity(runtime.factory, 168, at)
    assert result.total == 105 and result.interval == "day"
    assert [(row.label, row.count) for row in result.buckets[-2:]] == [("11-06", 55), ("11-07", 50)]


def test_empty_activity_api_and_range_validation(runtime: Runtime) -> None:
    with TestClient(create_app(runtime)) as client:
        response = client.get("/api/v1/workspace/publication-activity", params={"hours": 24})
        assert response.status_code == 200 and response.json()["total"] == 0
        assert (
            client.get("/api/v1/workspace/publication-activity", params={"hours": 721}).status_code
            == 422
        )
