"""Provider-neutral tool definitions and tool execution results."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from tau_agent.types import JSONValue


class ToolCancellationToken(Protocol):
    """Minimal cancellation interface accepted by tools."""

    def is_cancelled(self) -> bool:
        """Return whether tool execution should stop."""
        ...


class ToolExecutor(Protocol):
    """Async callable used to execute a tool."""

    def __call__(
        self,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
    ) -> Awaitable[AgentToolResult]:
        """Execute the tool with optional cancellation support."""
        ...


class ToolCall(BaseModel):
    """A request from the assistant to execute a named tool."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, JSONValue] = Field(default_factory=dict)
    # Opaque signature some providers (e.g. Gemini) require echoed back next
    # turn; ignored by providers that don't use it.
    thought_signature: str | None = None


class AgentToolResult(BaseModel):
    """Structured result returned by a tool execution."""

    model_config = ConfigDict(extra="forbid")

    tool_call_id: str
    name: str
    ok: bool
    content: str
    data: dict[str, JSONValue] | None = None
    details: dict[str, JSONValue] | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentTool:
    """A tool that can be exposed to an agent loop."""

    name: str
    description: str
    input_schema: Mapping[str, JSONValue]
    executor: ToolExecutor
    prompt_snippet: str | None = None
    prompt_guidelines: tuple[str, ...] = ()

    async def execute(
        self,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
    ) -> AgentToolResult:
        """Execute the tool with provider-neutral JSON-like arguments."""
        return await self.executor(arguments, signal=signal)
