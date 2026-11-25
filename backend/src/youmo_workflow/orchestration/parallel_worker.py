import json
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from pydantic import JsonValue

from youmo_workflow.domain.time import now_utc, stamp
from youmo_workflow.execution_limits import resource_budget
from youmo_workflow.orchestration.scheduler import Scheduler
from youmo_workflow.storage.models import Task


class Worker(Protocol):
    last_task: Task | None

    def once(self) -> bool: ...


class ParallelWorkers:
    def __init__(self, workers: list[Worker], record: Callable[[dict[str, JsonValue]], None]):
        if not workers:
            raise ValueError("At least one worker is required")
        self.workers, self.record = workers, record
        self.executor = ThreadPoolExecutor(
            max_workers=len(workers), thread_name_prefix="production"
        )
        self.pending: dict[int, tuple[Future[bool], float]] = {}
        self.next_ready = [0.0] * len(workers)

    def tick(self) -> None:
        now = time.monotonic()
        for index, (future, started) in list(self.pending.items()):
            if not future.done():
                continue
            del self.pending[index]
            worked = future.result()
            task = self.workers[index].last_task
            if worked:
                self.record(
                    {
                        "at": stamp(now_utc()),
                        "worker": index,
                        "task_id": task.id if task else None,
                        "kind": task.kind if task else None,
                        "batch_id": task.batch_id if task else None,
                        "elapsed_seconds": round(now - started, 3),
                        "started_at": stamp(now_utc() - timedelta(seconds=now - started)),
                    }
                )
            else:
                self.next_ready[index] = now + 1
        for index, worker in enumerate(self.workers):
            if index not in self.pending and now >= self.next_ready[index]:
                self.pending[index] = (self.executor.submit(worker.once), now)

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)


def serve_parallel(workers: list[Worker], scheduler: Scheduler, journal: Path) -> None:
    def record(event: dict[str, JsonValue]) -> None:
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, ensure_ascii=False) + "\n")

    record(
        {
            "event": "worker_started",
            "at": stamp(now_utc()),
            "limits": resource_budget().policy.model_dump(mode="json"),
        }
    )
    pool, next_tick = ParallelWorkers(workers, record), 0.0
    try:
        while True:
            if time.monotonic() >= next_tick:
                scheduler.tick()
                next_tick = time.monotonic() + 2
            pool.tick()
            time.sleep(0.2)
    finally:
        pool.close()
