"""Provider protocol the agent loop depends on.

This is the canonical definition of the contract between the portable agent
core and model adapters. ``tau_ai`` implements this protocol and
``tau_ai.provider`` re-exports it for backwards compatibility.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from tau_agent.messages import AgentMessage
from tau_agent.provider_events import ProviderEvent
from tau_agent.tools import AgentTool


class CancellationToken(Protocol):
    """Minimal cancellation interface accepted by providers."""

    def is_cancelled(self) -> bool:
        """Return whether the current stream should stop."""
        ...


class ModelProvider(Protocol):
    """Provider-neutral interface for streaming model responses."""

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Stream one model response as Tau provider events."""
        ...
