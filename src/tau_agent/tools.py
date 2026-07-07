"""Provider-neutral tool definitions and tool execution results."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from tau_agent.types import JSONValue


class ToolCancellationToken(Protocol):
    """Minimal cancellation interface accepted by tools."""

    def is_cancelled(self) -> bool:
        """Return whether tool execution should stop."""
        ...


class ToolUpdateCallback(Protocol):
    """Sync, fire-and-forget progress callback passed to opt-in executors.

    A tool reports live progress by calling this during execution. The loop
    bridges each call to a `ToolExecutionUpdateEvent(message, data)`. The
    payload is deliberately lighter than Pi's full-`AgentToolResult` partial:
    Tau's update event carries only a human-readable ``message`` and optional
    structured ``data`` (no ``content``/``details`` echo).

    Must be called from the event-loop thread. The bridge enqueues onto an
    ``asyncio.Queue``, which is not thread-safe; an executor that does work
    in a worker thread must hop back to the loop before reporting progress.
    """

    def __call__(self, message: str, data: dict[str, JSONValue] | None = None) -> None:
        """Report a progress update."""
        ...


class ToolCallRenderer(Protocol):
    """Optional display hook: render a tool call's arguments as one line.

    Mirrors Pi's `ToolDefinition.renderCall`, reduced to Tau's convention
    that renderers return plain strings rather than UI components. A
    frontend consults it to show a friendly invocation line (e.g. the
    subagent tool's `description` argument) instead of the generic
    `name arguments` fallback. Returning ``None`` falls back.
    """

    def __call__(self, arguments: Mapping[str, JSONValue]) -> str | None:
        """Return the display line for these arguments, or ``None``."""
        ...


class ToolResultRenderer(Protocol):
    """Optional display hook: render a tool result for the transcript.

    Mirrors Pi's `ToolDefinition.renderResult`, reduced to Tau's convention
    that renderers return plain (Rich-markup) strings rather than UI
    components. A frontend consults it once a result is attached to the tool
    row; the returned markup replaces the generic result block, while the
    invocation line (from `render_call`) stays. Running-state display
    (spinner/timer) remains with the host. ``expanded`` distinguishes the
    collapsed row from the expanded tool-results view. Returning ``None``
    falls back to the generic result block.
    """

    def __call__(self, result: AgentToolResult, *, expanded: bool) -> str | None:
        """Return display markup for this result, or ``None``."""
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


class ToolExecutorWithUpdate(Protocol):
    """Executor variant that also accepts a progress callback.

    Executors that want to report live progress declare an ``on_update``
    keyword parameter. `AgentTool.execute` detects this at construction time
    (via `inspect.signature`) and forwards the callback only to executors that
    accept it, so existing `ToolExecutor` implementations are untouched.
    """

    def __call__(
        self,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        *,
        on_update: ToolUpdateCallback | None = None,
    ) -> Awaitable[AgentToolResult]:
        """Execute the tool, optionally reporting progress via ``on_update``."""
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
    render_call: ToolCallRenderer | None = None
    render_result: ToolResultRenderer | None = None
    _accepts_on_update: bool = field(init=False, repr=False, compare=False, default=False)

    def __post_init__(self) -> None:
        # Detect the opt-in progress seam once, at construction. Executors that
        # declare an `on_update` parameter receive the progress callback;
        # every other executor keeps its original `(arguments, signal)` call.
        accepts = "on_update" in inspect.signature(self.executor).parameters
        object.__setattr__(self, "_accepts_on_update", accepts)

    async def execute(
        self,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        *,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        """Execute the tool with provider-neutral JSON-like arguments.

        ``on_update`` is forwarded only to executors that opt in by declaring an
        ``on_update`` parameter; for all other executors it is dropped so the
        original signature is preserved.
        """
        if on_update is not None and self._accepts_on_update:
            executor = cast("ToolExecutorWithUpdate", self.executor)
            return await executor(arguments, signal=signal, on_update=on_update)
        return await self.executor(arguments, signal=signal)
