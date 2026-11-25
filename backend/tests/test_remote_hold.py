import pytest
from conftest import Clock
from sqlalchemy import select

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.publication_cooldown import require_upload_slot
from youmo_workflow.features.remote_hold import SETTING, remote_held, set_remote_hold
from youmo_workflow.features.tasks import add_task
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Task, WorkflowSetting


def test_platform_hold_blocks_remote_jobs_but_not_local_production(
    runtime: Runtime, clock: Clock
) -> None:
    set_remote_hold(runtime.factory, True, "HTTP412: await official recovery", clock())
    with runtime.factory.begin() as db:
        for kind in ("publish", "finalize_private", "editing"):
            add_task(db, kind, "hold-test:" + kind, stamp(clock()))
    claimed = runtime.tasks.claim()
    assert claimed and claimed.kind == "editing"
    assert runtime.tasks.claim() is None
    with runtime.factory() as db:
        assert all(
            task.attempts == 0 for task in db.scalars(select(Task).where(Task.kind != "editing"))
        )
        with pytest.raises(WorkflowError) as error:
            require_upload_slot(db, "not-yet-submitted", clock())
        assert error.value.code == "REMOTE_ACCESS_BLOCKED"
    set_remote_hold(runtime.factory, False, "Verified platform recovery", clock())
    with runtime.factory() as db:
        assert not remote_held(db)
        assert db.get(WorkflowSetting, SETTING + ":history:1")
    claimed_remote = runtime.tasks.claim()
    assert claimed_remote and claimed_remote.kind in {"publish", "finalize_private"}
