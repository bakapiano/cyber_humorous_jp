import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import Clock, sample_video
from sqlalchemy import func, select

from youmo_workflow.config import Settings
from youmo_workflow.domain.batch_plan import BatchPlan
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.sources import video_dto
from youmo_workflow.orchestration.backlog import BacklogDrain, BacklogSnapshot
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task, Video, WorkflowSetting


@dataclass
class Disk:
    free: int = 100 * 1024**3


def available(_settings: Settings) -> bool:
    return True


def disk(_path: Path) -> Disk:
    return Disk()


def snapshot(runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Settings, "bilibili_ready", available)
    monkeypatch.setattr("youmo_workflow.orchestration.backlog.shutil.disk_usage", disk)
    runtime.sources.ingest(
        "tbsnewsdig", [sample_video(clock, f"{i:011d}") for i in range(201)], stamp(clock())
    )
    with runtime.factory() as db:
        items = [video_dto(row) for row in db.scalars(select(Video).order_by(Video.id))]
    value = BacklogSnapshot(
        captured_at=stamp(clock()),
        plan=BatchPlan(),
        plan_revision=0,
        policy=runtime.policy,
        items=items,
        existing_batches=[],
        rule="Do not count expiry as consumption",
    )
    path = runtime.settings.root / "data/backlog/test/manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(value.model_dump_json(), encoding="utf-8")
    return path


def stop_batch(runtime: Runtime, identifier: str) -> None:
    with runtime.factory.begin() as db:
        batch = db.get(Batch, identifier)
        assert batch
        batch.status = "failed"
        for task in db.scalars(select(Task).where(Task.batch_id == identifier)):
            task.status, task.error_code = "failed", "SYNTHETIC_FAILURE"


def test_frozen_backlog_survives_expiry_without_backdating_execution(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = snapshot(runtime, clock, monkeypatch)
    captured = stamp(clock())
    clock.at += timedelta(days=4)
    drain = BacklogDrain(runtime, path)
    batch = drain.enqueue_next(True)
    assert batch and batch.snapshot_at == captured and batch.candidate_count == 100
    with runtime.factory() as db:
        frozen = db.get(Batch, batch.id)
        assert frozen and frozen.execution_json
        assert json.loads(frozen.execution_json)["editing_revision"] == "v005"
    assert drain.enqueue_next(True) is None
    with runtime.factory() as db:
        task = db.scalar(select(Task).where(Task.batch_id == batch.id))
        assert task and task.created_at == stamp(clock())
        assert json.loads(task.input_json)["snapshot_at"] == captured
    stop_batch(runtime, batch.id)
    second = drain.enqueue_next(True)
    assert second and second.id != batch.id
    stop_batch(runtime, second.id)
    assert drain.enqueue_next(True) is None
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(Batch)) == 2
        assert all(row.status == "failed" for row in db.scalars(select(Batch)))
    assert (
        len(drain.snapshot.items) == 201
    )  # The unprocessed tail and failed batches are not erased.


def test_backlog_checks_source_identity(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = snapshot(runtime, clock, monkeypatch)
    with runtime.factory.begin() as db:
        video = db.get(Video, "00000000000")
        assert video
        video.channel_id = "different-source"
    with pytest.raises(WorkflowError, match="来源身份改变"):
        BacklogDrain(runtime, path).enqueue_next(True)
    with runtime.factory() as db:
        assert not db.scalar(select(Batch.id))


def test_backlog_refuses_outside_path(runtime: Runtime) -> None:
    with pytest.raises(WorkflowError, match="冻结目录"):
        BacklogDrain(runtime, runtime.settings.root.parent / "outside.json")


def test_backlog_does_not_repeat_an_already_reserved_slice(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = snapshot(runtime, clock, monkeypatch)
    first = BacklogDrain(runtime, path).enqueue_next(True)
    assert first
    stop_batch(runtime, first.id)
    altered = path.with_name("changed.json")
    data = BacklogSnapshot.model_validate_json(path.read_bytes())
    data.rule += " changed metadata"
    altered.write_text(data.model_dump_json(), encoding="utf-8")
    with pytest.raises(WorkflowError, match="已有其他处理记录"):
        BacklogDrain(runtime, altered).enqueue_next(True)


def unavailable(_settings: Settings) -> bool:
    return False


def low_disk(_path: Path) -> Disk:
    return Disk(free=19 * 1024**3)


@pytest.mark.parametrize("guard", ["login", "disk", "plan"])
def test_backlog_gates_stop_before_creating_tasks(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch, guard: str
) -> None:
    path = snapshot(runtime, clock, monkeypatch)
    if guard == "login":
        monkeypatch.setattr(Settings, "bilibili_ready", unavailable)
    elif guard == "disk":
        monkeypatch.setattr("youmo_workflow.orchestration.backlog.shutil.disk_usage", low_disk)
    else:
        with runtime.factory.begin() as db:
            db.add(
                WorkflowSetting(
                    id="quantity_batch_plan",
                    revision=1,
                    value_json=BatchPlan(publications_per_batch=9).model_dump_json(),
                )
            )
    with pytest.raises(WorkflowError) as error:
        BacklogDrain(runtime, path).enqueue_next(True)
    assert (
        error.value.code
        == {
            "login": "BILI_LOGIN_REQUIRED",
            "disk": "BACKLOG_DISK_LOW",
            "plan": "BACKLOG_PLAN_CHANGED",
        }[guard]
    )
    with runtime.factory() as db:
        assert not db.scalar(select(Batch.id))
        assert not db.scalar(select(Task.id))


def test_backlog_refuses_corrupt_cursor_lineage(
    runtime: Runtime, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = snapshot(runtime, clock, monkeypatch)
    drain = BacklogDrain(runtime, path)
    first = drain.enqueue_next(True)
    assert first
    stop_batch(runtime, first.id)
    with runtime.factory.begin() as db:
        batch = db.get(Batch, first.id)
        assert batch
        batch.run_key = "unrelated-run"
    with pytest.raises(WorkflowError, match="游标缺少匹配批次"):
        drain.enqueue_next(True)
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(Batch)) == 1
