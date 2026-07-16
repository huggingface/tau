"""Extension-facing API types and hook payloads."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from tau_agent.messages import AgentMessage
from tau_agent.tools import AgentTool, AgentToolResult
from tau_agent.types import JSONValue

if TYPE_CHECKING:
    from textual import events
    from textual.widget import Widget

    from tau_coding.extensions.runtime import ExtensionRuntime
    from tau_coding.tui.config import TuiTheme

AGENT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "agent_start",
        "agent_end",
        "agent_settled",
        "turn_start",
        "turn_end",
        "queue_update",
        "message_start",
        "message_update",
        "message_end",
        "tool_execution_start",
        "tool_execution_update",
        "tool_execution_end",
        "compaction_start",
        "compaction_end",
        "entry_appended",
        "session_info_changed",
        "thinking_level_changed",
        "auto_retry_start",
        "auto_retry_end",
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

# Host-side resolver installed into render paths: given a tool call's name and
# arguments, return the friendly invocation line from the tool's `render_call`
# or ``None`` to fall back to generic formatting. Errors are swallowed by the
# resolver, never raised into the frontend.
ToolCallMarkup = Callable[[str, "Mapping[str, JSONValue]"], "str | None"]

# Host-side resolver installed into render paths: given the tool name, its
# result, and whether the row is expanded, return the display markup from the tool's
# `render_result` or ``None`` to fall back to the generic result block. Errors
# are swallowed by the resolver, never raised into the frontend.
ToolResultMarkup = Callable[[str, AgentToolResult, bool], "str | None"]

# --- component seam ---------------------------------------------------------
# Widget-hosting capability that lets an extension mount its own Textual widgets
# into host-owned slots and a main-area view, and intercept keys pre-dispatch
# (before the host's priority bindings and the focused widget). The "component"
# type is Textual's own ``Widget`` (referenced only under TYPE_CHECKING so
# print-mode stays import-clean): Textual is deliberately part of the public
# extension contract. Extensions build against the Textual version tau pins; a
# Textual major bump is a coordinated break for core and extensions together.
# History and measured trade-offs: dev-notes/design/component-seam-experiment.md.
# It replaced the older transcript-source seam, which Step 3 removed from core.

Placement = Literal["above_prompt", "below_prompt"]

# Factories run on the UI thread and receive the live theme (theme handoff,
# mirrors Pi's ``(tui, theme) => Component``).
SlotWidgetFactory = Callable[["TuiTheme"], "Widget"]
# A slot widget may be given as a factory or, for the simple case, as a plain
# list of display lines the HOST turns into a widget — this lets an extension
# mount text without importing Textual at all (ports Pi's ``string[]`` form of
# ``setWidget``). Strings are rendered as Rich markup, with a literal-text
# fallback if the markup is malformed.
SlotWidgetContent = Sequence[str] | SlotWidgetFactory
# The main-view factory also receives the handle so the widget can close itself.
MainViewFactory = Callable[["MainViewHandle", "TuiTheme"], "Widget"]

# Pre-dispatch key hook (ports Pi's ``onTerminalInput``). Returns True to
# consume the key. Fires for every main-screen key regardless of focus; the host
# passes the Textual ``Key`` event and the current prompt text so the handler
# can self-gate (Pi gates on ``getEditorText() === ""``).
KeyInterceptor = Callable[["events.Key", str], bool]


_DEFAULT_THEME: TuiTheme | None = None


def _default_theme() -> TuiTheme:
    """Return a shared default theme without importing the TUI at module load.

    The import is deferred (and cached) so merely importing the extensions API
    stays free of the Textual/TUI dependency graph; only an extension that
    actually reads ``theme`` in print mode pays for it, and it never raises.
    """
    global _DEFAULT_THEME
    if _DEFAULT_THEME is None:
        from tau_coding.tui.config import TAU_DARK_THEME

        _DEFAULT_THEME = TAU_DARK_THEME
    return _DEFAULT_THEME


class MainViewHandle(Protocol):
    """Handle to an open main-area view (ports Pi's ``OverlayHandle``, trimmed).

    Carries Pi's ``done(result)`` semantics: the factory (or a key interceptor)
    calls ``close(result)`` to tear the view down *and* hand a value back to
    whoever opened it, and the opener awaits :meth:`wait` for that value. This
    is the result-resolution half of Pi's ``ctx.ui.custom<T>``, kept on the
    synchronous open/handle model rather than an ``async`` open.

    ``close()`` unmounts the view and restores the main transcript. It is safe
    to call more than once; the first close wins and later closes are no-ops.
    """

    def close(self, result: object | None = None) -> None:
        """Close the view, resolving :meth:`wait` with ``result`` (Pi's ``done``).

        The first close wins: its ``result`` is what :meth:`wait` returns, and
        any later ``close(...)`` is a no-op. Safe to call more than once.
        """
        ...

    async def wait(self) -> object | None:
        """Await the view's teardown and return the result passed to ``close``.

        Resolves with the value handed to :meth:`close` (``None`` when closed
        with no result). Also resolves with ``None`` — never hangs — when the
        view is force-cleared on a session rebind, quarantined after a widget
        crash, or superseded by a later ``open_main_view``. Returns immediately
        if the view was already closed before ``wait`` is awaited.
        """
        ...

    @property
    def is_open(self) -> bool: ...


class ComponentBridge(Protocol):
    """Host widget-hosting capability, exposed via ``context.ui.components``.

    Part of what a :class:`UiBridge` provides when a TUI is attached;
    :class:`NullUiBridge`/:class:`StderrUiBridge` implement it as no-ops so an
    extension stays fully functional (just widget-less) in print mode. Check
    :attr:`supports_components` before building widgets.
    """

    @property
    def supports_components(self) -> bool:
        """Return whether the frontend can host extension widgets."""
        ...

    @property
    def theme(self) -> TuiTheme:
        """Return the live TUI theme handed to widget factories."""
        ...

    def get_prompt_text(self) -> str:
        """Return the current prompt-editor text (Pi's getEditorText).

        Key interceptors receive the prompt text as their second argument;
        this is for reads outside the key path.
        """
        ...

    def request_render(self) -> None:
        """Ask the host to re-render mounted extension widgets (Pi's requestRender)."""
        ...

    def set_slot_widget(
        self,
        key: str,
        content: SlotWidgetContent | None,
        *,
        placement: Placement = "above_prompt",
    ) -> None:
        """Mount an extension widget into a prompt-adjacent slot under ``key``.

        ``content`` is either a ``factory(theme) -> Widget`` callable or a plain
        list of display lines (``Sequence[str]``) the host renders as Rich
        markup — the string form lets simple extensions avoid importing Textual.
        Passing ``content=None`` unmounts and forgets that key. Re-setting a key
        replaces its content. Multiple keys per placement mount in call order.
        Placement defaults to ``"above_prompt"`` (Pi's ``aboveEditor``).
        """
        ...

    def open_main_view(self, factory: MainViewFactory) -> MainViewHandle:
        """Mount ``factory(handle, theme)`` as a full main-area view.

        The widget replaces the main transcript in place (a display-toggled
        sibling, not a modal screen), so prompt-adjacent widgets such as slot
        widgets stay visible. ``handle.close(result)`` restores the transcript
        and resolves ``await handle.wait()`` with ``result`` (Pi's ``done``),
        so the opener can show a view and get an answer back.
        """
        ...

    def register_key_interceptor(self, handler: KeyInterceptor) -> Callable[[], None]:
        """Register a pre-dispatch key hook; return an unsubscribe callable.

        Ports Pi's ``onTerminalInput``. The handler sees a key before the
        host's app-level priority bindings and before the focused widget, so it
        can own navigation keys the host otherwise reserves. It fires for EVERY
        main-screen key regardless of focus (never while a modal screen is on
        top), so the handler MUST self-gate and return ``True`` only for keys it
        consumes.

        The host's hard interrupt/exit keys (``ctrl+c`` and ``ctrl+d``) are
        reserved: the interceptor is never consulted for them and cannot
        consume them, so a bug in the handler can never swallow the session's
        escape hatches. All other keys — escape, enter, arrows, tab — remain
        interceptable.
        """
        ...


class ExtensionError(RuntimeError):
    """Raised when an extension misuses the API (e.g. actions before binding)."""


_STALE_MESSAGE = (
    "extension instance is stale after reload: state captured before /reload"
    " (a saved `tau` API object, context, or ui handle) must not be reused;"
    " the reloaded extension received a fresh API in its new setup()"
)


class ExtensionGeneration:
    """Liveness token for one extension load generation.

    Ports Pi's ``assertActive``/``invalidate`` staleness guard: every
    :class:`ExtensionAPI` method and every :class:`ExtensionContext`/
    :class:`ExtensionUi` read checks this token before touching the runtime,
    so state captured before a `/reload` fails loudly instead of silently
    acting against the new registration set. Only reload invalidates; session
    rebinding (resume/new/branch) keeps the generation alive by design (see
    the phase-21 lifecycle Ruling).
    """

    __slots__ = ("_stale_message",)

    def __init__(self) -> None:
        self._stale_message: str | None = None

    @property
    def active(self) -> bool:
        """Return whether this generation is still the live one."""
        return self._stale_message is None

    def invalidate(self, message: str | None = None) -> None:
        """Mark this generation stale; the first message wins (Pi parity)."""
        if self._stale_message is None:
            self._stale_message = message or _STALE_MESSAGE

    def assert_active(self) -> None:
        """Raise :class:`ExtensionError` when this generation is stale."""
        if self._stale_message is not None:
            raise ExtensionError(self._stale_message)


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
    """Payload for the `input` hook: raw user prompt text, before expansion.

    Mirrors Pi's `InputEvent`. `source` says where the input came from:
    ``"interactive"`` for TUI/print-mode user input, ``"extension"`` for a turn
    an extension started via ``send_user_message``/``send_custom_message``.
    `streaming_behavior` says how the input will be queued when the agent is
    mid-run (``"steer"``/``"follow_up"``), and is ``None`` on the idle prompt
    path.

    Pi's `images` field is omitted (Tau has no image input yet) and Pi's
    ``"rpc"`` source is omitted (Tau has no RPC mode). Both defaults keep
    existing handlers that read only ``.text`` working unchanged.
    """

    text: str
    source: Literal["interactive", "extension"] = "interactive"
    streaming_behavior: Literal["steer", "follow_up"] | None = None


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
    details: dict[str, JSONValue] | None = None


ExtensionHandler = Callable[[object, "ExtensionContext"], object | Awaitable[object]]
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

    # -- component seam -- see ComponentBridge for docs -----------------------

    @property
    def supports_components(self) -> bool:
        """Return whether the frontend can host extension widgets."""
        ...

    @property
    def theme(self) -> TuiTheme:
        """Return the live TUI theme handed to widget factories."""
        ...

    def get_prompt_text(self) -> str:
        """Return the current prompt-editor text."""
        ...

    def request_render(self) -> None:
        """Ask the host to re-render mounted extension widgets."""
        ...

    def set_slot_widget(
        self,
        key: str,
        content: SlotWidgetContent | None,
        *,
        placement: Placement = "above_prompt",
    ) -> None:
        """Mount or remove an extension slot widget (factory or string lines)."""
        ...

    def open_main_view(self, factory: MainViewFactory) -> MainViewHandle:
        """Open a full main-area extension view."""
        ...

    def register_key_interceptor(self, handler: KeyInterceptor) -> Callable[[], None]:
        """Register a pre-dispatch key hook; return an unsubscribe callable.

        See the base bridge protocol: the handler is consulted before the
        host's priority bindings and the focused widget, fires for every
        main-screen key regardless of focus, and must self-gate. The hard
        interrupt/exit keys (``ctrl+c`` and ``ctrl+d``) are reserved and never
        reach the interceptor.
        """
        ...

    def clear_components(self) -> None:
        """Tear down all extension-owned UI (host-driven, not for extensions).

        The runtime drives this on `/reload` (the stale generation's widgets
        and interceptors must not outlive its registrations) and on session
        rebinds (resume/new), before ``session_start`` fires so handlers can
        re-mount. Slot widgets and any main view are unmounted (a pending
        ``wait()`` resolves with ``None``) and key interceptors are dropped.
        """
        ...


class _DeadMainViewHandle:
    """A no-op main-view handle returned when no UI can host a view."""

    def close(self, result: object | None = None) -> None:
        """Do nothing: there is no view to close (``result`` is ignored)."""

    async def wait(self) -> object | None:
        """Return None immediately: a dead handle never opens a view."""
        return None

    @property
    def is_open(self) -> bool:
        """Return False: a dead handle is never open."""
        return False


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

    # -- component seam -------------------------------------------------------

    @property
    def supports_components(self) -> bool:
        """Return False: print mode cannot host widgets."""
        return False

    @property
    def theme(self) -> TuiTheme:
        """Return a usable default theme (never raise; print-mode may read it)."""
        return _default_theme()

    def get_prompt_text(self) -> str:
        """Return an empty prompt: there is no editor in print mode."""
        return ""

    def request_render(self) -> None:
        """Do nothing: there is no frontend to re-render."""

    def set_slot_widget(
        self,
        key: str,
        content: SlotWidgetContent | None,
        *,
        placement: Placement = "above_prompt",
    ) -> None:
        """Do nothing: there is no slot to mount into."""

    def open_main_view(self, factory: MainViewFactory) -> MainViewHandle:
        """Return a dead handle: there is no main area to host a view."""
        return _DeadMainViewHandle()

    def register_key_interceptor(self, handler: KeyInterceptor) -> Callable[[], None]:
        """Return a no-op unsubscribe: no key stream to intercept."""
        return lambda: None

    def clear_components(self) -> None:
        """Do nothing: no components were ever mounted."""


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
    Every member (trivial reads included, matching Pi) asserts the owning
    load generation is still active and raises :class:`ExtensionError` when
    the facade was captured before a `/reload`.
    """

    def __init__(
        self,
        runtime: ExtensionRuntime,
        generation: ExtensionGeneration | None = None,
    ) -> None:
        self._runtime = runtime
        self._generation = generation if generation is not None else ExtensionGeneration()

    @property
    def has_ui(self) -> bool:
        """Return whether an interactive UI is attached."""
        self._generation.assert_active()
        return self._runtime.ui.has_ui

    async def select(
        self,
        title: str,
        options: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> str | None:
        """Prompt the user to pick an option; None on cancel/no UI."""
        self._generation.assert_active()
        return await self._runtime.ui.select(title, options, timeout=timeout)

    async def confirm(
        self,
        title: str,
        message: str,
        *,
        timeout: float | None = None,
    ) -> bool:
        """Ask the user to confirm; True only if confirmed."""
        self._generation.assert_active()
        return await self._runtime.ui.confirm(title, message, timeout=timeout)

    async def input(
        self,
        title: str,
        placeholder: str = "",
        *,
        timeout: float | None = None,
    ) -> str | None:
        """Prompt the user for text; None on cancel/no UI."""
        self._generation.assert_active()
        return await self._runtime.ui.input(title, placeholder, timeout=timeout)

    @property
    def components(self) -> ComponentBridge:
        """Return the host widget-hosting capability.

        Straight pass-through to the installed UI bridge, which implements the
        :class:`ComponentBridge` members (the TUI hosts real widgets; the
        print-mode bridges are no-ops with ``supports_components == False``).
        Gate widget work on ``context.ui.components.supports_components``.
        A stale facade raises here, before the bridge is ever reachable.
        """
        self._generation.assert_active()
        return cast("ComponentBridge", self._runtime.ui)

    def notify(self, message: str, level: NotifyLevel = "info") -> None:
        """Show a notification in the UI, if one is attached."""
        self._generation.assert_active()
        self._runtime.ui.notify(message, level)


class ExtensionContext:
    """Read-only session context exposed to extensions.

    Every property (trivial reads included, matching Pi's context getters)
    asserts the owning load generation is still active, so a context captured
    before a `/reload` raises :class:`ExtensionError` instead of reading the
    reloaded world.
    """

    def __init__(
        self,
        runtime: ExtensionRuntime,
        generation: ExtensionGeneration | None = None,
    ) -> None:
        self._runtime = runtime
        self._generation = generation if generation is not None else ExtensionGeneration()
        self._ui = ExtensionUi(runtime, self._generation)

    @property
    def cwd(self) -> Path:
        """Return the session working directory."""
        self._generation.assert_active()
        return self._runtime.session_view.cwd

    @property
    def model(self) -> str:
        """Return the active model name."""
        self._generation.assert_active()
        return self._runtime.session_view.model

    @property
    def provider_name(self) -> str:
        """Return the active provider name."""
        self._generation.assert_active()
        return self._runtime.session_view.provider_name

    @property
    def session_id(self) -> str | None:
        """Return the current session id, if the session is indexed."""
        self._generation.assert_active()
        return self._runtime.session_view.session_id

    @property
    def system_prompt(self) -> str:
        """Return the active system prompt."""
        self._generation.assert_active()
        return self._runtime.session_view.system_prompt

    @property
    def is_running(self) -> bool:
        """Return whether an agent run is currently active."""
        self._generation.assert_active()
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
        self._generation.assert_active()
        messages = self._runtime.session_view.messages
        return tuple(message.model_copy(deep=True) for message in messages)

    @property
    def has_ui(self) -> bool:
        """Return whether an interactive UI is attached."""
        self._generation.assert_active()
        return self._runtime.ui.has_ui

    @property
    def ui(self) -> ExtensionUi:
        """Return the interactive UI facade (Pi's `ctx.ui`).

        Use `await context.ui.select/confirm/input(...)` to drive dialogs.
        Because command handlers are sync (see the docs), a `/command` that
        needs a dialog should spawn a loop task that awaits `context.ui`.
        """
        self._generation.assert_active()
        return self._ui


class ExtensionAPI:
    """The object handed to each extension's `setup(tau)` entry point.

    Every method and property asserts the load generation first (Pi's
    ``assertActive`` parity): after `/reload` replaces the registration set,
    a `tau` object captured by the previous instance raises
    :class:`ExtensionError` on any use instead of silently acting against
    the new world.
    """

    def __init__(
        self,
        runtime: ExtensionRuntime,
        extension_name: str,
        generation: ExtensionGeneration | None = None,
    ) -> None:
        self._runtime = runtime
        self._extension_name = extension_name
        self._generation = generation if generation is not None else ExtensionGeneration()
        self._context = ExtensionContext(runtime, self._generation)

    @property
    def name(self) -> str:
        """Return this extension's name."""
        self._generation.assert_active()
        return self._extension_name

    @property
    def context(self) -> ExtensionContext:
        """Return read-only session context."""
        self._generation.assert_active()
        return self._context

    def register_tool(self, tool: AgentTool) -> None:
        """Register an agent tool (first registration per name wins)."""
        self._generation.assert_active()
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
        self._generation.assert_active()
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
        self._generation.assert_active()
        self._runtime.register_prompt_guideline(self._extension_name, guideline)

    def on(
        self,
        event: str,
        handler: ExtensionHandler | None = None,
    ) -> Callable[[ExtensionHandler], ExtensionHandler] | ExtensionHandler:
        """Subscribe to an event, directly or as a decorator."""
        self._generation.assert_active()
        if handler is not None:
            self._runtime.subscribe(self._extension_name, event, handler)
            return handler

        def decorator(decorated: ExtensionHandler) -> ExtensionHandler:
            self._generation.assert_active()
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
        self._generation.assert_active()
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
        self._generation.assert_active()
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
        self._generation.assert_active()
        self._runtime.send_custom_message(
            content,
            custom_type=custom_type,
            details=details,
            deliver_as=deliver_as,
            trigger_turn=trigger_turn,
        )

    async def append_entry(self, namespace: str, data: dict[str, JSONValue]) -> None:
        """Persist extension-owned data to the session as a custom entry."""
        self._generation.assert_active()
        await self._runtime.append_custom_entry(namespace, data)

    def notify(self, message: str, level: NotifyLevel = "info") -> None:
        """Show a notification in the UI, if one is attached."""
        self._generation.assert_active()
        self._runtime.ui.notify(message, level)


@dataclass(slots=True)
class RegisteredExtension:
    """Book-keeping for one loaded extension inside the runtime."""

    name: str
    path: Path
    api: ExtensionAPI
    handlers: dict[str, list[ExtensionHandler]] = field(default_factory=dict)
