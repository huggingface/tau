"""Extension-facing API types and hook payloads."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from tau_agent.messages import AgentMessage
from tau_agent.tools import AgentTool, AgentToolResult
from tau_agent.types import JSONValue

if TYPE_CHECKING:
    from tau_coding.extensions.runtime import ExtensionRuntime

AGENT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "agent_start",
        "agent_end",
        "turn_start",
        "turn_end",
        "retry",
        "queue_update",
        "message_start",
        "message_delta",
        "thinking_delta",
        "message_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
        "error",
    }
)
AGENT_EVENT_WILDCARD = "agent_event"

LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "session_start",
        "session_shutdown",
        "input",
        "tool_call",
        "tool_result",
    }
)

SessionLifecycleReason = Literal["startup", "reload", "new", "resume", "branch", "quit"]
DeliverAs = Literal["steer", "follow_up"]
NotifyLevel = Literal["info", "warning", "error"]


@dataclass(frozen=True, slots=True)
class CustomMessageView:
    """Read-only view of a custom message handed to a message renderer.

    Ports Pi's ``CustomMessage``: ``custom_type`` selects the renderer,
    ``content`` is the LLM-context text, and ``details`` carries arbitrary
    structured data the renderer formats.
    """

    custom_type: str
    content: str
    details: Mapping[str, JSONValue] | None = None


@dataclass(frozen=True, slots=True)
class MessageRenderOptions:
    """Options passed to a message renderer (ports Pi's ``MessageRenderOptions``)."""

    expanded: bool = False


# A message renderer returns a Rich-markup (or plain) string, NOT a Textual
# widget, so extensions never import the TUI toolkit (deviation from Pi's
# ``Component`` return; see the phase-21 custom-renderer Ruling).
MessageRenderer = Callable[[CustomMessageView, MessageRenderOptions], str]

# Host-side resolver installed into render paths: given a custom message's
# fields and whether it is expanded, return rendered markup or ``None`` to fall
# back to the raw content. Errors are swallowed by the resolver, never raised
# into the frontend.
CustomMessageMarkup = Callable[[str, str, "Mapping[str, JSONValue] | None", bool], "str | None"]


class ExtensionError(RuntimeError):
    """Raised when an extension misuses the API (e.g. actions before binding)."""


@dataclass(frozen=True, slots=True)
class SessionStartEvent:
    """Payload for the `session_start` lifecycle event."""

    reason: SessionLifecycleReason


@dataclass(frozen=True, slots=True)
class SessionShutdownEvent:
    """Payload for the `session_shutdown` lifecycle event."""

    reason: SessionLifecycleReason


@dataclass(frozen=True, slots=True)
class InputEvent:
    """Payload for the `input` hook: user prompt text after expansion."""

    text: str


@dataclass(frozen=True, slots=True)
class InputHookResult:
    """Result of an `input` hook handler.

    `action="continue"` leaves the text unchanged, `"transform"` replaces it
    with `text` (transforms chain across handlers), and `"handled"` consumes
    the input entirely, optionally showing `message` to the user.
    """

    action: Literal["continue", "transform", "handled"] = "continue"
    text: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallHookEvent:
    """Payload for the `tool_call` hook, before a tool executes.

    Carries no tool-call id: the hook runs inside the tool executor seam,
    which the agent loop invokes without the id. Use the observation events
    (`tool_execution_start`/`tool_execution_end`) for id correlation.
    """

    tool_name: str
    arguments: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class ToolCallHookResult:
    """Result of a `tool_call` hook handler.

    Set `block=True` (with an optional `reason`) to prevent execution, or
    return replacement `arguments` to rewrite the call. Blocking wins over
    argument rewrites and short-circuits remaining handlers.
    """

    block: bool = False
    reason: str | None = None
    arguments: Mapping[str, JSONValue] | None = None


@dataclass(frozen=True, slots=True)
class ToolResultHookEvent:
    """Payload for the `tool_result` hook, after a tool executes."""

    tool_name: str
    arguments: Mapping[str, JSONValue]
    result: AgentToolResult


@dataclass(frozen=True, slots=True)
class ToolResultHookResult:
    """Result of a `tool_result` hook handler; set fields to override."""

    content: str | None = None
    ok: bool | None = None
    details: dict[str, JSONValue] | None = None


ExtensionHandler = Callable[[object], object | Awaitable[object]]
# Command handlers are sync-only: the slash-command path (CommandRegistry ->
# CodingSession.handle_command -> TUI submit) is synchronous end to end.
ExtensionCommandHandler = Callable[["str", "ExtensionCommandContext"], "str | None"]


@dataclass(frozen=True, slots=True)
class ExtensionRuntimeDiagnostic:
    """A runtime failure raised by an extension handler."""

    extension: str
    event: str
    message: str


class UiBridge(Protocol):
    """Host-provided UI capabilities available to extensions.

    Dialog methods (`select`/`confirm`/`input`) are async and mirror Pi's
    `ctx.ui`. Without an interactive frontend they return the Pi no-op
    defaults (`None`/`False`/`None`). `timeout` (seconds) auto-dismisses a
    dialog with the no-op default; `None` waits indefinitely.
    """

    @property
    def has_ui(self) -> bool:
        """Return whether an interactive UI is attached."""
        ...

    def notify(self, message: str, level: NotifyLevel = "info") -> None:
        """Show a notification to the user (no-op without a UI)."""
        ...

    async def select(
        self,
        title: str,
        options: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> str | None:
        """Show a picker; return the chosen option, or None on cancel."""
        ...

    async def confirm(
        self,
        title: str,
        message: str,
        *,
        timeout: float | None = None,
    ) -> bool:
        """Show a confirmation; return True only if confirmed."""
        ...

    async def input(
        self,
        title: str,
        placeholder: str = "",
        *,
        timeout: float | None = None,
    ) -> str | None:
        """Show a text prompt; return the entered text, or None on cancel."""
        ...


class NullUiBridge:
    """UI bridge used when no interactive frontend is attached."""

    @property
    def has_ui(self) -> bool:
        """Return False: print mode has no interactive UI."""
        return False

    def notify(self, message: str, level: NotifyLevel = "info") -> None:
        """Ignore notifications without a UI."""

    async def select(
        self,
        title: str,
        options: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> str | None:
        """Return None: no UI to pick from (Pi no-op default)."""
        return None

    async def confirm(
        self,
        title: str,
        message: str,
        *,
        timeout: float | None = None,
    ) -> bool:
        """Return False: no UI to confirm with (Pi no-op default)."""
        return False

    async def input(
        self,
        title: str,
        placeholder: str = "",
        *,
        timeout: float | None = None,
    ) -> str | None:
        """Return None: no UI to enter text into (Pi no-op default)."""
        return None


class StderrUiBridge(NullUiBridge):
    """UI bridge that writes extension notifications to stderr (print mode).

    Inherits the Pi no-op dialog defaults from `NullUiBridge`; only
    `notify` is observable in print mode.
    """

    def notify(self, message: str, level: NotifyLevel = "info") -> None:
        """Print the notification to stderr."""
        print(f"[extension:{level}] {message}", file=sys.stderr)


@dataclass(frozen=True, slots=True)
class ExtensionCommandContext:
    """Context passed to extension slash-command handlers."""

    name: str
    args: str
    api: ExtensionAPI


class ExtensionUi:
    """Interactive UI facade exposed to extensions as `context.ui`.

    Mirrors Pi's `ctx.ui`: async `select`/`confirm`/`input` dialogs plus a
    synchronous `notify`. Every call delegates to the host UI bridge, which
    returns the Pi no-op defaults when no interactive frontend is attached.
    """

    def __init__(self, runtime: ExtensionRuntime) -> None:
        self._runtime = runtime

    @property
    def has_ui(self) -> bool:
        """Return whether an interactive UI is attached."""
        return self._runtime.ui.has_ui

    async def select(
        self,
        title: str,
        options: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> str | None:
        """Prompt the user to pick an option; None on cancel/no UI."""
        return await self._runtime.ui.select(title, options, timeout=timeout)

    async def confirm(
        self,
        title: str,
        message: str,
        *,
        timeout: float | None = None,
    ) -> bool:
        """Ask the user to confirm; True only if confirmed."""
        return await self._runtime.ui.confirm(title, message, timeout=timeout)

    async def input(
        self,
        title: str,
        placeholder: str = "",
        *,
        timeout: float | None = None,
    ) -> str | None:
        """Prompt the user for text; None on cancel/no UI."""
        return await self._runtime.ui.input(title, placeholder, timeout=timeout)

    def notify(self, message: str, level: NotifyLevel = "info") -> None:
        """Show a notification in the UI, if one is attached."""
        self._runtime.ui.notify(message, level)


class ExtensionContext:
    """Read-only session context exposed to extensions."""

    def __init__(self, runtime: ExtensionRuntime) -> None:
        self._runtime = runtime
        self._ui = ExtensionUi(runtime)

    @property
    def cwd(self) -> Path:
        """Return the session working directory."""
        return self._runtime.session_view.cwd

    @property
    def model(self) -> str:
        """Return the active model name."""
        return self._runtime.session_view.model

    @property
    def provider_name(self) -> str:
        """Return the active provider name."""
        return self._runtime.session_view.provider_name

    @property
    def session_id(self) -> str | None:
        """Return the current session id, if the session is indexed."""
        return self._runtime.session_view.session_id

    @property
    def system_prompt(self) -> str:
        """Return the active system prompt."""
        return self._runtime.session_view.system_prompt

    @property
    def is_running(self) -> bool:
        """Return whether an agent run is currently active."""
        return self._runtime.session_view.is_running

    @property
    def transcript(self) -> tuple[AgentMessage, ...]:
        """Return the active-path parent conversation as read-only copies.

        Mirrors the read access Pi extensions get via
        ``ctx.sessionManager.getBranch()``: the user/assistant/tool messages on
        the current branch, with compaction and branch summaries already folded
        in as ``UserMessage`` entries (Tau has no separate summary message
        type). Each message is deep-copied so an extension mutating a returned
        object cannot corrupt the live session transcript.
        """
        messages = self._runtime.session_view.messages
        return tuple(message.model_copy(deep=True) for message in messages)

    @property
    def has_ui(self) -> bool:
        """Return whether an interactive UI is attached."""
        return self._runtime.ui.has_ui

    @property
    def ui(self) -> ExtensionUi:
        """Return the interactive UI facade (Pi's `ctx.ui`).

        Use `await context.ui.select/confirm/input(...)` to drive dialogs.
        Because command handlers are sync (see the docs), a `/command` that
        needs a dialog should spawn a loop task that awaits `context.ui`.
        """
        return self._ui


class ExtensionAPI:
    """The object handed to each extension's `setup(tau)` entry point."""

    def __init__(self, runtime: ExtensionRuntime, extension_name: str) -> None:
        self._runtime = runtime
        self._extension_name = extension_name
        self._context = ExtensionContext(runtime)

    @property
    def name(self) -> str:
        """Return this extension's name."""
        return self._extension_name

    @property
    def context(self) -> ExtensionContext:
        """Return read-only session context."""
        return self._context

    def register_tool(self, tool: AgentTool) -> None:
        """Register an agent tool (first registration per name wins)."""
        self._runtime.register_tool(self._extension_name, tool)

    def register_command(
        self,
        name: str,
        handler: ExtensionCommandHandler,
        *,
        description: str = "",
        usage: str | None = None,
        aliases: tuple[str, ...] = (),
    ) -> None:
        """Register a slash command backed by this extension."""
        self._runtime.register_command(
            self._extension_name,
            name,
            handler,
            description=description,
            usage=usage,
            aliases=aliases,
        )

    def add_prompt_guideline(self, guideline: str) -> None:
        """Add a standalone guideline line to the system prompt.

        Tool-attached guidance belongs on the tool (`prompt_snippet`,
        `prompt_guidelines`); this is for behavioral guidance not tied to
        any tool. Duplicate lines are de-duplicated at prompt build time.
        """
        self._runtime.register_prompt_guideline(self._extension_name, guideline)

    def on(
        self,
        event: str,
        handler: ExtensionHandler | None = None,
    ) -> Callable[[ExtensionHandler], ExtensionHandler] | ExtensionHandler:
        """Subscribe to an event, directly or as a decorator."""
        if handler is not None:
            self._runtime.subscribe(self._extension_name, event, handler)
            return handler

        def decorator(decorated: ExtensionHandler) -> ExtensionHandler:
            self._runtime.subscribe(self._extension_name, event, decorated)
            return decorated

        return decorator

    def send_user_message(
        self,
        content: str,
        *,
        deliver_as: DeliverAs = "follow_up",
    ) -> None:
        """Queue a user message for the active or next agent run."""
        self._runtime.send_user_message(content, deliver_as=deliver_as)

    def register_message_renderer(
        self,
        custom_type: str,
        renderer: MessageRenderer,
    ) -> None:
        """Register a renderer for custom messages with this ``custom_type``.

        Ports Pi's ``registerMessageRenderer``: the first registration per
        ``custom_type`` wins. The renderer receives a :class:`CustomMessageView`
        and :class:`MessageRenderOptions` and returns a Rich-markup string; it
        must not return a Textual widget (that keeps extensions TUI-free).
        """
        self._runtime.register_message_renderer(self._extension_name, custom_type, renderer)

    def send_custom_message(
        self,
        content: str,
        *,
        custom_type: str,
        details: dict[str, JSONValue] | None = None,
        deliver_as: DeliverAs = "follow_up",
        trigger_turn: bool = True,
    ) -> None:
        """Send a custom message that renders via a registered renderer.

        Ports Pi's ``sendMessage``: ``content`` still enters LLM context, while
        ``custom_type``/``details`` let a registered renderer format the
        transcript block. With ``trigger_turn`` (the default) the message starts
        a turn when the session is idle, mirroring ``send_user_message``; set it
        to ``False`` to only queue for the next run.
        """
        self._runtime.send_custom_message(
            content,
            custom_type=custom_type,
            details=details,
            deliver_as=deliver_as,
            trigger_turn=trigger_turn,
        )

    async def append_entry(self, namespace: str, data: dict[str, JSONValue]) -> None:
        """Persist extension-owned data to the session as a custom entry."""
        await self._runtime.append_custom_entry(namespace, data)

    def notify(self, message: str, level: NotifyLevel = "info") -> None:
        """Show a notification in the UI, if one is attached."""
        self._runtime.ui.notify(message, level)


@dataclass(slots=True)
class RegisteredExtension:
    """Book-keeping for one loaded extension inside the runtime."""

    name: str
    path: Path
    api: ExtensionAPI
    handlers: dict[str, list[ExtensionHandler]] = field(default_factory=dict)
