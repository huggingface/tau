"""Portable completed-turn context and host scheduling decisions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from tau_agent.messages import AgentMessage, AssistantMessage, ToolResultMessage
from tau_agent.provider import CancellationToken


@dataclass(frozen=True, slots=True)
class AgentTurnContext:
    """Finalized turn with shallow transcript snapshots, before ``turn_end``."""

    message: AssistantMessage
    tool_results: tuple[ToolResultMessage, ...]
    messages: tuple[AgentMessage, ...]
    new_messages: tuple[AgentMessage, ...]


@dataclass(frozen=True, slots=True)
class AgentTurnDecision:
    """End before queue polling, or ensure one subsequent provider request."""

    action: Literal["end", "continue"]


type FinishTurn = Callable[
    [AgentTurnContext, CancellationToken | None],
    AgentTurnDecision | None | Awaitable[AgentTurnDecision | None],
]
