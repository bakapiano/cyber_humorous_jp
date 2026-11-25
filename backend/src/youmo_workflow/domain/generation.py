from collections.abc import Callable
from typing import Protocol, runtime_checkable

from youmo_workflow.domain.content import GenerationProvider, GenerationRequest


@runtime_checkable
class CheckedGeneration(Protocol):
    def generate_checked(
        self, request: GenerationRequest, validate: Callable[[str], object]
    ) -> str: ...


def checked_generation(
    provider: GenerationProvider, request: GenerationRequest, validate: Callable[[str], object]
) -> str:
    if isinstance(provider, CheckedGeneration):
        return provider.generate_checked(request, validate)
    raw = provider.generate(request)
    validate(raw)
    return raw
