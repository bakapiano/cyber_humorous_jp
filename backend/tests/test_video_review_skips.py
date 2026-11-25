import pytest
from conftest import Assets, Clock
from test_hard_subtitles import NoNativeGateway, burned

from youmo_workflow.domain.batch_plan import BatchExecution
from youmo_workflow.domain.errors import WorkflowError
from youmo_workflow.domain.finalization import FinalizePrivate
from youmo_workflow.domain.remote import RemoteSubmission
from youmo_workflow.domain.time import stamp
from youmo_workflow.features.review_skips import verified_review_skips
from youmo_workflow.features.tasks import add_task
from youmo_workflow.orchestration.publication import PublicationExecutor
from youmo_workflow.runtime import Runtime
from youmo_workflow.storage.models import Batch, Task


class ReviewGateway(NoNativeGateway):
    state: int | None = -2
    description = "审核未通过"

    def inspect(self, bvid: str) -> RemoteSubmission:
        result = super().inspect(bvid)
        result.archive.state = self.state
        result.archive.state_desc = self.description
        return result


def uploaded(runtime: Runtime, assets: Assets, clock: Clock) -> str:
    value = burned(runtime, assets, clock)
    value.rights_confirmed = True
    publication = runtime.publications.prepare(value)
    runtime.publications.confirm(publication.id, publication.manifest_hash)
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, publication.id)
        row.status, row.remote_id = "uploaded", "BV1234567890"
    return publication.id


def test_explicit_platform_rejection_is_retained_and_skippable(
    runtime: Runtime, assets: Assets, clock: Clock
) -> None:
    identifier = uploaded(runtime, assets, clock)
    with runtime.factory.begin() as db:
        row = runtime.publications.require(db, identifier)
        db.add(
            Batch(
                id="review-batch",
                local_date="2026-11-06",
                snapshot_at=stamp(clock()),
                policy_json="{}",
                candidate_count=50,
                execution_json=BatchExecution(
                    batch_size=50,
                    publications_per_batch=1,
                    plan_revision=1,
                    trigger="manual",
                    skip_review_rejections=True,
                ).model_dump_json(),
            )
        )
        task = add_task(db, "finalize_private", "review-finalize:" + identifier, stamp(clock()))
        task.batch_id = "review-batch"
        task.input_json = FinalizePrivate(
            publication_id=row.id, manifest_hash=row.manifest_hash
        ).model_dump_json()
    claimed = runtime.tasks.claim(("finalize_private",))
    assert claimed
    gateway = ReviewGateway(2)
    with pytest.raises(WorkflowError) as error:
        PublicationExecutor(runtime, gateway).reconcile(identifier)
    assert error.value.code == "VIDEO_REVIEW_REJECTED" and gateway.writes == 0
    assert runtime.tasks.fail(claimed, error.value)
    with runtime.factory() as db:
        row = runtime.publications.require(db, identifier)
        assert row.status == "video_rejected" and row.remote_id == "BV1234567890"
        stored = db.get(Task, claimed.id)
        assert stored and stored.status == "skipped"
        assert verified_review_skips(db, "review-batch", [stored]) == {"abcdefghijk"}


@pytest.mark.parametrize("state", [None, -20, -4])
def test_unknown_review_state_never_becomes_completed_or_rejected(
    runtime: Runtime, assets: Assets, clock: Clock, state: int | None
) -> None:
    identifier = uploaded(runtime, assets, clock)
    gateway = ReviewGateway(2)
    gateway.state, gateway.description = state, "待核实"
    with pytest.raises(WorkflowError) as error:
        PublicationExecutor(runtime, gateway).reconcile(identifier)
    assert error.value.code == "REMOTE_REVIEW_STATUS_UNKNOWN"
    with runtime.factory() as db:
        assert runtime.publications.require(db, identifier).status == "uploaded"
    assert gateway.writes == 0
