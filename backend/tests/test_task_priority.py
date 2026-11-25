from datetime import timedelta

from conftest import Clock

from youmo_workflow.domain.time import stamp
from youmo_workflow.features.tasks import add_task
from youmo_workflow.runtime import Runtime


def test_downstream_first_is_opt_in_and_keeps_same_stage_fifo(
    runtime: Runtime, clock: Clock
) -> None:
    with runtime.factory.begin() as db:
        add_task(db, "prepare_video", "first", stamp(clock()))
        add_task(db, "editing", "edit-first", stamp(clock() + timedelta(seconds=1)))
        add_task(db, "editing", "edit-next", stamp(clock() + timedelta(seconds=2)))
    clock.at += timedelta(seconds=3)
    first = runtime.tasks.claim()
    assert first and first.unique_key == "first"
    runtime.tasks.finish(first)
    for key in ("edit-first", "edit-next"):
        task = runtime.tasks.claim(downstream_first=True)
        assert task and task.unique_key == key
        runtime.tasks.finish(task)


def test_downstream_can_overlap_upstream_without_reordering_prerequisites(
    runtime: Runtime, clock: Clock
) -> None:
    with runtime.factory.begin() as db:
        for index, kind in enumerate(("prepare_video", "packaging", "editing")):
            add_task(db, kind, kind, stamp(clock() + timedelta(seconds=index)))
    clock.at += timedelta(seconds=3)
    for kind in ("editing", "packaging", "prepare_video"):
        task = runtime.tasks.claim(downstream_first=True)
        assert task and task.kind == kind
        runtime.tasks.finish(task)


def test_priority_never_bypasses_remote_serialization_or_retry_time(
    runtime: Runtime, clock: Clock
) -> None:
    with runtime.factory.begin() as db:
        for kind in ("publish", "finalize_private", "editing", "prepare_publication"):
            task = add_task(db, kind, kind, stamp(clock()))
            if kind == "prepare_publication":
                task.status = "retry_wait"
                task.next_run_at = stamp(clock() + timedelta(minutes=5))
        failed = add_task(db, "cleanup_batch", "failed-cleanup", stamp(clock()))
        failed.status = "failed"
    finalize = runtime.tasks.claim(downstream_first=True)
    assert finalize and finalize.kind == "finalize_private"
    edit = runtime.tasks.claim(downstream_first=True)
    assert edit and edit.kind == "editing"
    assert runtime.tasks.claim(downstream_first=True) is None
    runtime.tasks.finish(finalize)
    upload = runtime.tasks.claim(downstream_first=True)
    assert upload and upload.kind == "publish"
    assert runtime.tasks.claim(downstream_first=True) is None
