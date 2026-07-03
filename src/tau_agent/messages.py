"""Provider-neutral transcript message models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tau_agent.tools import ToolCall
from tau_agent.types import JSONValue


class UsageCost(BaseModel):
    """Billed cost breakdown for a single provider response, in USD.

    Ports Pi's `Usage.cost`. Populated only when a per-model pricing table is
    available; Tau has none yet, so providers leave the enclosing ``Usage.cost``
    set to ``None`` (see the phase-21 ruling).
    """

    model_config = ConfigDict(extra="forbid")

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


class Usage(BaseModel):
    """Real billed token usage reported by a provider for one assistant response.

    Ports Pi's `Usage` interface (packages/ai/src/types.ts) to snake_case. Token
    counts are the provider's billed figures, not local estimates. ``reasoning``
    is a subset of ``output`` (already included in it). ``cache_write_1h`` is a
    subset of ``cache_write`` and is only reported by Anthropic.
    """

    model_config = ConfigDict(extra="forbid")

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cache_write_1h: int | None = None
    reasoning: int | None = None
    total_tokens: int = 0
    cost: UsageCost | None = None


class UserMessage(BaseModel):
    """A message authored by the user."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user"] = "user"
    content: str


class AssistantMessage(BaseModel):
    """A message authored by the assistant, optionally requesting tool calls."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage | None = None


class ToolResultMessage(BaseModel):
    """A transcript message containing the result of a previous tool call."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    content: str
    ok: bool = True
    data: dict[str, JSONValue] | None = None
    details: dict[str, JSONValue] | None = None
    error: str | None = None


type AgentMessage = UserMessage | AssistantMessage | ToolResultMessage
