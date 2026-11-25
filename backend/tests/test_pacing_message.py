from datetime import timedelta

import pytest
from conftest import Clock

from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.batch_plan import BatchPlanService
from youmo_workflow.features.publication_cooldown import (
    MINIMUM_GAP,
    clear_rejections,
    record_rejection,
    require_upload_slot,
)
from youmo_workflow.runtime import Runtime


def test_success_pacing_is_not_reported_as_platform_rate_limit(
    runtime: Runtime, clock: Clock
) -> None:
    with runtime.factory.begin() as db:
        record_rejection(db, clock(), "137022")
        clear_rejections(db, clock())
        with pytest.raises(WorkflowError, match="投稿间隔或限流冷却"):
            require_upload_slot(db, "synthetic", clock())
    service = BatchPlanService(runtime.factory, runtime.policy, clock)
    state = service.state()
    assert "正常投稿间隔" in state.message and "上传限流" not in state.message
    assert state.next_submit_at == stamp(clock() + MINIMUM_GAP)
    clock.at += MINIMUM_GAP
    assert service.state().next_submit_at is None
    assert "正常投稿间隔" not in service.state().message


@pytest.mark.parametrize("count", [1, 5])
def test_real_rejection_remains_labeled_and_keeps_effective_gate(
    runtime: Runtime, clock: Clock, count: int
) -> None:
    with runtime.factory.begin() as db:
        for _ in range(count):
            record_rejection(db, clock(), "137022")
    state = BatchPlanService(runtime.factory, runtime.policy, clock).state()
    expected = timedelta(hours=1) if count == 5 else MINIMUM_GAP
    assert "B站上传限流" in state.message and "正常投稿间隔" not in state.message
    assert state.next_submit_at == stamp(clock() + expected)
