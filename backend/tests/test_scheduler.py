from datetime import timedelta

from conftest import Clock, sample_video
from selection_support import discovery_items
from test_batch_trigger import configure, finish_batch

from youmo_workflow.domain.time import stamp
from youmo_workflow.orchestration.scan_scheduler import ScanScheduler
from youmo_workflow.orchestration.scheduler import Scheduler
from youmo_workflow.runtime import Runtime


def finish_syncs(runtime: Runtime) -> None:
    while task := runtime.tasks.claim():
        runtime.tasks.finish(task)


def test_scheduler_creates_only_after_full_batch_at_any_time(
    runtime: Runtime, clock: Clock
) -> None:
    clock.at -= timedelta(hours=2)
    configure(runtime, clock, auto=True)
    runtime.sources.ingest("tbsnewsdig", discovery_items(49), stamp(clock()))
    scheduler = Scheduler(runtime.factory, runtime.policy, clock)
    scheduler.tick()
    assert not runtime.batches.list_batches()
    runtime.sources.ingest("tbsnewsdig", discovery_items(100), stamp(clock()))
    scheduler.tick()
    scheduler.tick()
    assert len(runtime.batches.list_batches()) == 2
    finish_batch(runtime, runtime.batches.list_batches()[0].id)
    clock.at += timedelta(seconds=1)
    scheduler.tick()
    assert len(runtime.batches.list_batches()) == 2


def test_collection_switch_does_not_authorize_production(runtime: Runtime, clock: Clock) -> None:
    runtime.sources.ingest("tbsnewsdig", [sample_video(clock)], stamp(clock()))
    scheduler = ScanScheduler(runtime.factory, runtime.policy, clock)
    scheduler.tick()
    finish_syncs(runtime)
    scheduler.tick()
    assert not runtime.batches.list_batches()


def test_auto_switch_is_hot_loaded_and_disabling_stops_next_batch(
    runtime: Runtime, clock: Clock
) -> None:
    runtime.sources.ingest("tbsnewsdig", discovery_items(100), stamp(clock()))
    scheduler = Scheduler(runtime.factory, runtime.policy, clock)
    scheduler.tick()
    assert not runtime.batches.list_batches()
    configure(runtime, clock, auto=True)
    scheduler.tick()
    assert len(runtime.batches.list_batches()) == 1
    configure(runtime, clock, auto=False)
    finish_batch(runtime, runtime.batches.list_batches()[0].id)
    scheduler.tick()
    assert len(runtime.batches.list_batches()) == 1
