import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from conftest import Clock
from pydantic import JsonValue

from youmo_workflow.domain.time import stamp
from youmo_workflow.execution_limits import PerformancePolicy, ResourceBudget
from youmo_workflow.features.tasks import add_task
from youmo_workflow.orchestration.parallel_worker import ParallelWorkers, Worker
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task


def test_resource_budget_allows_bounded_overlap_and_releases_on_error() -> None:
    budget = ResourceBudget(PerformancePolicy(enabled=True, workers=4, model=2))
    barrier = Barrier(2, timeout=5)

    def work(index: int) -> int:
        with budget.acquire("model"):
            barrier.wait()
            return index

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sorted(executor.map(work, range(4))) == list(range(4))
    assert budget.peak["model"] == 2 and budget.active["model"] == 0
    with pytest.raises(RuntimeError), budget.acquire("model"):
        raise RuntimeError("synthetic failure")
    assert budget.active["model"] == 0


class TwoJobs:
    last_task: Task | None = None

    def __init__(self, barrier: Barrier):
        self.barrier, self.calls = barrier, 0

    def once(self) -> bool:
        self.calls += 1
        if self.calls != 1:
            return False
        self.barrier.wait()
        return True


def test_pool_executes_simultaneously_and_records_finished_work() -> None:
    barrier = Barrier(2, timeout=5)
    workers: list[Worker] = [TwoJobs(barrier), TwoJobs(barrier)]
    records: list[dict[str, JsonValue]] = []
    pool = ParallelWorkers(workers, records.append)
    deadline = time.monotonic() + 5
    try:
        while len(records) < 2 and time.monotonic() < deadline:
            pool.tick()
            time.sleep(0.01)
        assert len(records) == 2
        assert {value for row in records if isinstance(value := row["worker"], int)} == {0, 1}
    finally:
        pool.close()


def test_remote_writes_serialize_while_other_work_can_proceed(
    runtime: Runtime, clock: Clock
) -> None:
    with runtime.factory.begin() as db:
        for kind in ("publish", "finalize_private", "packaging"):
            add_task(db, kind, "synthetic:" + kind, stamp(clock()))
    upload = runtime.tasks.claim(("publish",))
    assert upload
    assert runtime.tasks.claim(("finalize_private",)) is None
    preparation = runtime.tasks.claim(("packaging",))
    assert preparation
    runtime.tasks.finish(upload)
    assert runtime.tasks.claim(("finalize_private",))


def test_parallel_claims_are_unique(runtime: Runtime, clock: Clock) -> None:
    with runtime.factory.begin() as db:
        for index in range(12):
            add_task(db, "packaging", f"synthetic:{index}", stamp(clock()))

    def claim(_index: int) -> Task | None:
        return runtime.tasks.claim(("packaging",))

    with ThreadPoolExecutor(max_workers=6) as executor:
        tasks = list(executor.map(claim, range(12)))
    assert all(tasks) and len({task.id for task in tasks if task}) == 12
