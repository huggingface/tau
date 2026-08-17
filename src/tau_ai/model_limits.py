"""Optional runtime model-limit discovery contracts for provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RuntimeModelLimits:
    """Provider-reported limits for one model on the active serving surface."""

    context_window: int
    max_output_tokens: int | None = None
    effective_context_window_percent: int = 100
    auto_compact_token_limit: int | None = None

    def __post_init__(self) -> None:
        if self.context_window <= 0:
            raise ValueError("context_window must be positive")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if not 1 <= self.effective_context_window_percent <= 100:
            raise ValueError("effective_context_window_percent must be between 1 and 100")
        if self.auto_compact_token_limit is not None and self.auto_compact_token_limit <= 0:
            raise ValueError("auto_compact_token_limit must be positive")

    @property
    def effective_context_window(self) -> int:
        """Return the provider's usable window after its requested headroom."""
        return max(1, self.context_window * self.effective_context_window_percent // 100)

    @property
    def effective_auto_compact_token_limit(self) -> int:
        """Return an explicit limit or the Codex-compatible 90% default."""
        default_limit = max(1, self.context_window * 9 // 10)
        if self.auto_compact_token_limit is None:
            return min(default_limit, self.effective_context_window)
        return min(self.auto_compact_token_limit, self.effective_context_window)


@runtime_checkable
class ModelLimitsProvider(Protocol):
    """Optional provider capability for serving-surface-specific model limits."""

    async def discover_model_limits(self, model: str) -> RuntimeModelLimits | None:
        """Return live limits for ``model``, or ``None`` when it is not advertised."""
        ...
