"""Unified reasoning policy for workflow model requests."""

from typing import Literal

ReasoningEffort = Literal["high", "max"]
DEFAULT_REASONING_EFFORT: ReasoningEffort = "high"
