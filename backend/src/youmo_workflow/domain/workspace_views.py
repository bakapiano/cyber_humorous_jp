from typing import Literal

from youmo_workflow.domain.models import BatchDTO, Contract, TaskDTO
from youmo_workflow.domain.publication import PublicationDTO


class PageInfo(Contract):
    total: int
    offset: int
    limit: int


class BatchPage(PageInfo):
    items: list[BatchDTO]


class TaskPage(PageInfo):
    items: list[TaskDTO]


class PublicationPage(PageInfo):
    items: list[PublicationDTO]


class ActivityBucket(Contract):
    start: str
    label: str
    count: int = 0


class PublicationActivity(Contract):
    since: str
    until: str
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    interval: Literal["hour", "day"]
    total: int
    buckets: list[ActivityBucket]
