"""Process-wide resource budgets shared by all production worker threads."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from functools import wraps
from threading import BoundedSemaphore, Lock
from typing import Literal

from pydantic import Field

from youmo_workflow.config import get_settings
from youmo_workflow.domain.models import Contract

Resource = Literal["model", "download", "asr", "render"]


class PerformancePolicy(Contract):
    enabled: bool = False
    workers: int = Field(default=1, ge=1, le=8)
    model: int = Field(default=1, ge=1, le=6)
    download: int = Field(default=1, ge=1, le=4)
    asr: Literal[1] = 1
    render: int = Field(default=1, ge=1, le=2)


class ResourceBudget:
    def __init__(self, policy: PerformancePolicy):
        self.policy = policy
        self.slots: dict[Resource, BoundedSemaphore] = {
            name: BoundedSemaphore(getattr(policy, name) if policy.enabled else 1)
            for name in ("model", "download", "asr", "render")
        }
        self.lock = Lock()
        self.active: dict[Resource, int] = dict.fromkeys(self.slots, 0)
        self.peak: dict[Resource, int] = dict.fromkeys(self.slots, 0)

    @contextmanager
    def acquire(self, name: Resource) -> Generator[None]:
        with self.slots[name]:
            with self.lock:
                self.active[name] += 1
                self.peak[name] = max(self.peak[name], self.active[name])
            try:
                yield
            finally:
                with self.lock:
                    self.active[name] -= 1


_budget: ResourceBudget | None = None
_initialization = Lock()


def resource_budget() -> ResourceBudget:
    global _budget
    with _initialization:
        if _budget is None:
            path = get_settings().root / "config/performance.json"
            policy = (
                PerformancePolicy.model_validate_json(path.read_bytes())
                if path.exists()
                else PerformancePolicy()
            )
            _budget = ResourceBudget(policy)
        return _budget


def limited[**P, R](resource: Resource) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def call(*args: P.args, **kwargs: P.kwargs) -> R:
            with resource_budget().acquire(resource):
                return function(*args, **kwargs)

        return call

    return decorate
