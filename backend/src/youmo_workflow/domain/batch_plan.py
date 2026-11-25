from typing import Literal
from uuid import UUID

from pydantic import Field

from youmo_workflow.domain.models import Contract

PublicationMode = Literal["private_reprint", "public_reprint"]


class BatchPlan(Contract):
    batch_size: int = Field(default=100, ge=50, le=1000, strict=True)
    publications_per_batch: int = Field(default=10, ge=1, le=30, strict=True)
    auto_enabled: bool = False
    publication_mode: PublicationMode = "private_reprint"


class BatchPlanUpdate(BatchPlan):
    revision: int = Field(ge=0)
    confirm_execution: bool = False


class BatchPlanState(BatchPlan):
    revision: int
    eligible_count: int
    active_batch_id: str | None = None
    automatic_blocked: bool = False
    message: str
    publisher_ready: bool = True
    buffer_used: int = 0
    buffer_limit: int = 50
    next_submit_at: str | None = None


class BatchTrigger(Contract):
    request_id: UUID
    revision: int = Field(ge=0)
    confirm_execution: Literal[True]


class BatchExecution(Contract):
    batch_size: int = Field(ge=50, le=1000)
    publications_per_batch: int = Field(ge=1, le=30)
    plan_revision: int = Field(ge=0)
    trigger: Literal["manual", "automatic"]
    publication_mode: PublicationMode = "private_reprint"
    execution_authorized: Literal[True] = True
    producer_consumer: bool = Field(default=False, exclude_if=lambda value: not value)
    skip_review_rejections: bool = Field(default=False, exclude_if=lambda value: not value)
    editing_revision: Literal["v004", "v005"] = Field(
        default="v004", exclude_if=lambda value: value == "v004"
    )


class ProductionDispatch(Contract):
    batch_id: str
    video_ids: list[str]
    publications_per_batch: int
