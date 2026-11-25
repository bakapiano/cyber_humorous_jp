import json

from conftest import Assets, Clock
from test_delivery_completion import delivered

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.production_buffer import buffer_used
from youmo_workflow.features.tasks import add_task
from youmo_workflow.orchestration.cleanup import BatchMediaCleanup
from youmo_workflow.orchestration.finalization import PrivateFinalizer
from youmo_workflow.publisher_worker import PublisherWorker
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task


def test_single_delivery_cleans_while_another_item_is_failed(
    runtime: Runtime, clock: Clock, assets: Assets
) -> None:
    task, gateway = delivered(runtime, clock, assets)
    batch_id = "independent-cleanup"
    task.batch_id = batch_id
    with runtime.factory.begin() as db:
        db.add(
            Batch(
                id=batch_id,
                local_date=clock().date().isoformat(),
                snapshot_at=stamp(clock()),
                status="failed",
                candidate_count=100,
                policy_json=runtime.policy.model_dump_json(),
                execution_json=BatchExecution(
                    batch_size=100,
                    publications_per_batch=2,
                    plan_revision=0,
                    trigger="manual",
                    producer_consumer=True,
                ).model_dump_json(),
            )
        )
        row = db.get(Task, task.id)
        assert row
        row.batch_id = batch_id
        failed = add_task(db, "publish", "other-unknown", stamp(clock()))
        failed.batch_id, failed.status = batch_id, "failed"
    output = PrivateFinalizer(runtime, gateway).run(task)
    assert runtime.tasks.finish(task, output)
    cleanup_task = runtime.tasks.claim(("cleanup_publication",))
    assert cleanup_task
    directory = runtime.artifacts.root / "production/test"
    directory.mkdir(parents=True)
    video, log = directory / "original.mp4", directory / "model-output.json"
    video.write_bytes(b"synthetic video")
    log.write_text("keep", encoding="utf-8")
    outcome = BatchMediaCleanup(runtime).run(cleanup_task)
    assert runtime.tasks.finish(cleanup_task, outcome)
    assert json.loads(outcome)["video_files"] == 1
    assert not video.exists() and log.read_text(encoding="utf-8") == "keep"
    assert gateway.writes == 1  # No remote review or extra upload.


def test_consumer_never_claims_local_production(runtime: Runtime, clock: Clock) -> None:
    from test_remote import FakeGateway

    with runtime.factory.begin() as db:
        add_task(db, "prepare_video", "producer-only", stamp(clock()))
    consumer = PublisherWorker(runtime, FakeGateway())
    assert not consumer.once()
    task = runtime.tasks.claim(("prepare_video",))
    assert task and task.status == "running"


def test_success_does_not_free_reserved_capacity_until_cleanup(
    runtime: Runtime, clock: Clock
) -> None:
    with runtime.factory.begin() as db:
        db.add(
            Batch(
                id="reserved",
                local_date=clock().date().isoformat(),
                snapshot_at=stamp(clock()),
                status="running",
                candidate_count=100,
                policy_json=runtime.policy.model_dump_json(),
                execution_json=BatchExecution(
                    batch_size=100,
                    publications_per_batch=10,
                    plan_revision=0,
                    trigger="manual",
                    producer_consumer=True,
                ).model_dump_json(),
            )
        )
        finalizer = add_task(db, "finalize_private", "receipt", stamp(clock()))
        finalizer.batch_id, finalizer.status = "reserved", "succeeded"
    with runtime.factory() as db:
        assert buffer_used(db) == 10
    with runtime.factory.begin() as db:
        cleanup = add_task(db, "cleanup_publication", "cleaned", stamp(clock()))
        cleanup.batch_id, cleanup.status = "reserved", "succeeded"
    with runtime.factory() as db:
        assert buffer_used(db) == 9
