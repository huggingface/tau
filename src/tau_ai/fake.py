"""Deterministic model provider for tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

from tau_agent.messages import AgentMessage
from tau_agent.tools import AgentTool
from tau_ai.events import ProviderEvent
from tau_ai.provider import CancellationToken


class FakeProvider:
    """A provider that replays predefined event streams.

    Each call to `stream_response` consumes the next scripted stream. This gives
    agent-loop tests deterministic model behavior without network access.
    """

    def __init__(self, streams: Iterable[Iterable[ProviderEvent]]) -> None:
        self._streams = [list(stream) for stream in streams]
        self.calls: list[tuple[str, str, list[AgentMessage], list[AgentTool]]] = []

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Replay the next scripted stream."""
        self.calls.append((model, system, list(messages), list(tools)))
        stream = self._streams.pop(0) if self._streams else []

        async def iterator() -> AsyncIterator[ProviderEvent]:
            for event in stream:
                if signal is not None and signal.is_cancelled():
                    return
                yield event

        return iterator()
