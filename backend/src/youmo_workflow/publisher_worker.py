"""Independent serial delivery consumer. No source scanning or model initialization."""

import argparse
import time

from youmo_workflow.adapters.biliup import BiliupGateway
from youmo_workflow.config import get_settings
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.remote import PublicationGateway
from youmo_workflow.features.batch_progress import refresh_batch_progress
from youmo_workflow.features.production_buffer import CONSUMER_KINDS
from youmo_workflow.orchestration.batch_publication import BatchPublisher
from youmo_workflow.orchestration.cleanup import BatchMediaCleanup
from youmo_workflow.orchestration.finalization import PrivateFinalizer
from youmo_workflow.runtime import Runtime, build_runtime, initialize
from youmo_workflow.storage.models import Task


class PublisherWorker:
    def __init__(self, runtime: Runtime, gateway: PublicationGateway):
        self.runtime = runtime
        self.publisher = BatchPublisher(runtime, gateway)
        self.finalizer = PrivateFinalizer(runtime, gateway)
        self.cleanup = BatchMediaCleanup(runtime)
        self.last_task: Task | None = None

    def once(self) -> bool:
        tasks = self.runtime.tasks
        tasks.recover()
        task = tasks.claim(CONSUMER_KINDS, downstream_first=True)
        self.last_task = task
        if task is None:
            return False
        try:
            if task.kind == "publish":
                output = self.publisher.run(task)
            elif task.kind == "finalize_private":
                output = self.finalizer.run(task)
            else:
                output = self.cleanup.run(task)
            tasks.finish(task, output)
        except WorkflowError as error:
            tasks.fail(task, error)
        except Exception:
            tasks.fail(
                task, WorkflowError("INTERNAL_ERROR", "投稿消费者异常，保留回执，不盲目重投。")
            )
        refresh_batch_progress(self.runtime.factory, task.batch_id, tasks.clock())
        return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    runtime = build_runtime(get_settings())
    initialize(runtime)
    gateway = BiliupGateway(
        runtime.settings.root.parent
        / "3_bilibili_upload/bin/biliupR-v1.3.4-x86_64-windows/biliup.exe",
        runtime.settings.bilibili_credentials,
        runtime.settings.artifact_root / "publication_receipts",
    )
    worker = PublisherWorker(runtime, gateway)
    while True:
        worked = worker.once()
        if args.once:
            return
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    main()
