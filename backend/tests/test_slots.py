from datetime import timedelta
from uuid import uuid4

import pytest
from conftest import Assets, Clock
from test_publications import manifest

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.publications import manifest_hash
from youmo_workflow.features.slots import SlotService
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Publication


def insert_confirmed(runtime: Runtime, clock: Clock, assets: Assets) -> str:
    value = manifest(assets, clock)
    value.content_id = uuid4().hex
    value.visibility, value.test_only, value.rights_confirmed = "public", False, True
    digest, publication_id = manifest_hash(value), uuid4().hex
    with runtime.factory.begin() as db:
        db.add(
            Publication(
                id=publication_id,
                content_id=value.content_id,
                manifest_json=value.model_dump_json(),
                manifest_hash=digest,
                confirmed_hash=digest,
                status="confirmed",
                created_at=stamp(clock()),
            )
        )
    return publication_id


def test_slot_reservation_is_idempotent(runtime: Runtime, clock: Clock, assets: Assets) -> None:
    clock.at += timedelta(hours=2)
    publication_id = insert_confirmed(runtime, clock, assets)
    slots = SlotService(runtime.factory, runtime.policy, clock)
    assert slots.reserve(publication_id) == slots.reserve(publication_id)


def test_two_publications_cannot_take_one_slot(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    clock.at += timedelta(hours=2)
    slots = SlotService(runtime.factory, runtime.policy, clock)
    slots.reserve(insert_confirmed(runtime, clock, assets))
    with pytest.raises(WorkflowError, match="额度"):
        slots.reserve(insert_confirmed(runtime, clock, assets))


def test_missed_slot_not_replayed(runtime: Runtime, clock: Clock, assets: Assets) -> None:
    clock.at += timedelta(hours=2, minutes=1)
    with pytest.raises(WorkflowError, match="错过"):
        SlotService(runtime.factory, runtime.policy, clock).reserve(
            insert_confirmed(runtime, clock, assets)
        )
