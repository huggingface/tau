"""Minimal Textual app for Tau coding sessions."""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum, auto
from inspect import isawaitable
from io import StringIO
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, TypeVar, cast

from rich.console import Console, Group
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingsMap
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Key, Resize
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TextArea,
)
from textual.worker import Worker

from tau_agent.events import (
    AgentEndEvent,
    AgentStartEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from tau_agent.messages import (
    AgentMessage,
    AssistantMessage,
    CustomMessage,
    TextContent,
    ThinkingContent,
    UserMessage,
)
from tau_agent.provider import CancellationToken
from tau_agent.provider_events import (
    AssistantErrorEvent,
    AssistantMessageEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
)
from tau_agent.tools import AgentTool
from tau_agent.types import JSONValue
from tau_coding.catalog_loader import save_user_catalog_entries
from tau_coding.commands import (
    LOGIN_PROVIDER_ALIASES,
    CommandRegistry,
    create_default_command_registry,
    format_reload_summary,
)
from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.events import AutoRetryStartEvent, CodingSessionEvent, QueueUpdateEvent
from tau_coding.extensions.api import (
    KeyInterceptor,
    MainViewFactory,
    MainViewHandle,
    Placement,
    SlotWidgetContent,
    SlotWidgetFactory,
)
from tau_coding.oauth import login_openai_codex
from tau_coding.oauth_registry import get_oauth_provider, oauth_provider_ids
from tau_coding.oauth_types import (
    OAuthAuthInfo,
    OAuthDeviceCodeInfo,
    OAuthLoginCallbacks,
    OAuthPrompt,
    OAuthSelectPrompt,
)
from tau_coding.provider_catalog import (
    BUILTIN_PROVIDER_CATALOG,
    ProviderCatalogEntry,
    builtin_provider_entry,
)
from tau_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderConfig,
    ProviderSelection,
    load_provider_settings,
    provider_config_from_catalog_entry,
    provider_has_usable_credentials,
    resolve_provider_selection,
    resolve_startup_thinking_level,
    save_provider_settings,
    upsert_openai_compatible_provider,
    upsert_saved_provider,
)
from tau_coding.provider_runtime import create_model_provider
from tau_coding.resources import TauResourcePaths
from tau_coding.session import (
    TREE_RUNNING_MESSAGE,
    CodingSession,
    CodingSessionConfig,
    ModelChoice,
    SessionTreeBranchResult,
    SessionTreeChoice,
    jsonl_session_storage,
    parse_terminal_command,
)
from tau_coding.session_manager import CodingSessionRecord, SessionManager
from tau_coding.shell_config import load_shell_settings
from tau_coding.tui.adapter import TuiEventAdapter
from tau_coding.tui.autocomplete import (
    CompletionItem,
    CompletionOption,
    CompletionState,
    build_completion_state,
)
from tau_coding.tui.config import (
    TAU_DARK_THEME,
    TuiKeybindings,
    TuiSettings,
    TuiTheme,
    TuiThemeName,
    load_tui_settings,
    save_tui_settings,
)
from tau_coding.tui.state import TuiState, format_terminal_command_result_block
from tau_coding.tui.terminal_title import TerminalTitleController
from tau_coding.tui.themes import (
    available_tui_theme_names,
    get_tui_theme,
    load_custom_tui_themes,
    set_custom_tui_themes,
)
from tau_coding.tui.widgets import (
    CompactSessionInfo,
    SessionSidebar,
    TranscriptView,
    _custom_markup_to_text,
    render_completion_suggestions,
)

type BindingEntry = Binding | tuple[str, str] | tuple[str, str, str]
SIDEBAR_MIN_WIDTH = 96
SIDEBAR_MIN_HEIGHT = 38
ACTIVITY_TICK_SECONDS = 0.15
ACTIVITY_COLOR_FADE_STEPS = 24
ACTIVITY_INDICATOR_HEIGHT = 3
COMPLETION_MAX_VISIBLE_LINES = 16
COMPLETION_INITIAL_TERMINAL_FRACTION = 3
COMPLETION_MIN_TRANSCRIPT_LINES = 4
COMPLETION_WIDGET_CHROME_LINES = 3
PROMPT_PLACEHOLDER = "Ask Tau…  Enter submits, Shift+Enter inserts a newline"
NO_STORED_CREDENTIALS_MESSAGE = (
    "No stored credentials to remove. /logout only removes credentials saved by /login; "
    "environment variables and providers.json config are unchanged."
)


class LoginRequiredProvider:
    """Placeholder provider used so the TUI can open before login."""

    def __init__(self, message: str) -> None:
        self.message = message

    async def aclose(self) -> None:
        """Close provider resources."""

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        """Surface a login-needed provider error."""
        del system, messages, tools, signal

        async def iterator() -> AsyncIterator[AssistantMessageEvent]:
            error = AssistantMessage(
                model=model,
                stop_reason="error",
                error_message=self.message,
            )
            yield AssistantErrorEvent(reason="error", error=error)

        return iterator()


_DialogResult = TypeVar("_DialogResult")


class _TuiExtensionUiBridge:
    """Route extension UI requests to the running Textual app."""

    _SEVERITIES: ClassVar[dict[str, Literal["information", "warning", "error"]]] = {
        "info": "information",
        "warning": "warning",
        "error": "error",
    }

    def __init__(self, app: TauTuiApp) -> None:
        self._app = app

    @property
    def has_ui(self) -> bool:
        """Return True: an interactive TUI is attached."""
        return True

    def notify(self, message: str, level: str = "info") -> None:
        """Show an extension notification through the app's dedupe path."""
        self._app._notify(message, severity=self._SEVERITIES.get(level, "information"))

    async def select(
        self,
        title: str,
        options: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> str | None:
        """Show a modal picker; return the choice, or None on cancel/timeout."""
        theme = self._app.tui_settings.resolved_theme
        screen: ModalScreen[str | None] = ExtensionSelectScreen(title, options, theme=theme)
        return await self._run_dialog(screen, default=None, timeout=timeout)

    async def confirm(
        self,
        title: str,
        message: str,
        *,
        timeout: float | None = None,
    ) -> bool:
        """Show a modal confirmation; True only if confirmed."""
        theme = self._app.tui_settings.resolved_theme
        screen: ModalScreen[bool] = ExtensionConfirmScreen(title, message, theme=theme)
        return await self._run_dialog(screen, default=False, timeout=timeout)

    async def input(
        self,
        title: str,
        placeholder: str = "",
        *,
        timeout: float | None = None,
    ) -> str | None:
        """Show a modal text prompt; return the text, or None on cancel/timeout."""
        theme = self._app.tui_settings.resolved_theme
        screen: ModalScreen[str | None] = ExtensionInputScreen(title, placeholder, theme=theme)
        return await self._run_dialog(screen, default=None, timeout=timeout)

    # -- component seam -- pass-through to the app ----------------------------

    @property
    def supports_components(self) -> bool:
        """Return True: a Textual TUI can host extension widgets."""
        return True

    @property
    def theme(self) -> TuiTheme:
        """Return the live TUI theme handed to widget factories."""
        return self._app.tui_settings.resolved_theme

    def get_prompt_text(self) -> str:
        """Return the current prompt-editor text (Pi's getEditorText).

        Interceptors do not need this — the host passes the prompt text as
        their second argument; it exists for reads outside the key path.
        """
        return self._app._current_prompt_text()

    def request_render(self) -> None:
        """Re-render mounted extension widgets (analog of Pi's requestRender)."""
        self._app._refresh_extension_components()

    def set_slot_widget(
        self,
        key: str,
        content: SlotWidgetContent | None,
        *,
        placement: Placement = "above_prompt",
    ) -> None:
        """Mount or remove an extension slot widget by key (factory or lines)."""
        self._app._set_extension_slot_widget(key, content, placement)

    def open_main_view(self, factory: MainViewFactory) -> MainViewHandle:
        """Open a full main-area extension view (display-toggled, not modal)."""
        return self._app._open_extension_main_view(factory)

    def register_key_interceptor(self, handler: KeyInterceptor) -> Callable[[], None]:
        """Register a pre-dispatch key hook; return an unsubscribe callable.

        Ports Pi's ``onTerminalInput``. The handler is consulted in
        ``TauTuiApp.on_event`` before Textual's app-level priority bindings and
        before the focused widget receives the key, so it can own navigation
        keys (``up``/``down``/``tab``/…) that tau otherwise binds with
        ``priority=True``. Because it fires for EVERY main-screen key regardless
        of which widget holds focus, the handler MUST self-gate (e.g. on the
        prompt text and its own state) and return ``True`` only for keys it
        actually consumes. It is never consulted while a modal screen (dialog,
        picker, command palette) is on top.
        """
        return self._app._register_extension_key_interceptor(handler)

    def clear_components(self) -> None:
        """Tear down all extension-owned UI (runtime-driven: /reload, rebind)."""
        self._app._clear_extension_components()

    async def _run_dialog(
        self,
        screen: ModalScreen[_DialogResult],
        *,
        default: _DialogResult,
        timeout: float | None,
    ) -> _DialogResult:
        """Push a modal and await its dismissal via a callback-resolved future.

        Uses ``push_screen(screen, callback)`` + an ``asyncio.Future`` rather
        than ``push_screen_wait`` (which requires a Textual worker context);
        this pattern works from any coroutine on the app's event loop,
        including a task spawned by a sync ``/command`` handler. On ``timeout``
        (seconds) the dialog auto-dismisses and the no-op ``default`` returns.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[_DialogResult] = loop.create_future()

        def _resolve(result: _DialogResult | None) -> None:
            # Textual passes None when a screen is dismissed with no value;
            # map that (and any explicit cancel) to the no-op default.
            if not future.done():
                future.set_result(default if result is None else result)

        self._app.push_screen(screen, _resolve)
        if timeout is None:
            return await future
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            # `Screen.dismiss` only works while the dialog is the top screen.
            # Known limitation: if another screen was pushed on top before the
            # timeout fired, the stale dialog stays on the stack (its future
            # result is discarded by `_resolve` racing `future.done()`) until
            # the covering screen closes and the user dismisses it manually.
            if screen.is_current:
                with suppress(Exception):
                    screen.dismiss(default)
            return default


class _MainViewHandle:
    """Host-side handle to an open extension main view.

    ``close(result)`` is idempotent and routes back to the app, which unmounts
    the widget and restores the main transcript; it also resolves ``wait()``
    with ``result`` (Pi's ``done(result)``). Every other teardown path the host
    owns — session rebind, quarantine, being superseded by a later
    ``open_main_view`` — resolves ``wait()`` with ``None`` via
    :meth:`_resolve`, so an awaiting extension task never hangs.
    """

    def __init__(self, app: TauTuiApp, result: asyncio.Future[object | None]) -> None:
        self._app = app
        self._open = True
        self.widget: Widget | None = None
        # Created on the app's event loop at open time; resolved exactly once by
        # the first teardown (close/clear/quarantine/supersede) to wake wait().
        self._result = result

    def close(self, result: object | None = None) -> None:
        """Close the view, resolving ``wait()`` with ``result`` (safe to repeat)."""
        if not self._open:
            return
        self._open = False
        self._resolve(result)
        self._app._close_extension_main_view(self)

    def _resolve(self, result: object | None) -> None:
        """Resolve the pending ``wait()`` future once; later calls are no-ops."""
        if not self._result.done():
            self._result.set_result(result)

    async def wait(self) -> object | None:
        """Await teardown and return the ``close`` result (``None`` if cleared)."""
        return await self._result

    @property
    def is_open(self) -> bool:
        """Return whether the view is still open."""
        return self._open


class _DeadMainViewHandle:
    """A no-op main-view handle returned when a view could not be opened."""

    def close(self, result: object | None = None) -> None:
        """Do nothing: there is no view to close (``result`` is ignored)."""

    async def wait(self) -> object | None:
        """Return None immediately: a dead handle never opens a view."""
        return None

    @property
    def is_open(self) -> bool:
        """Return False: a dead handle is never open."""
        return False


class CompletionActionTarget(Protocol):
    """App actions used by the prompt input completion bindings."""

    def action_accept_completion(self) -> None: ...

    def action_cancel(self) -> None: ...

    def action_completion_next(self) -> None: ...

    def action_completion_previous(self) -> None: ...

    def action_open_command_palette(self) -> None: ...

    def action_open_session_picker(self) -> None: ...

    def action_cycle_thinking(self) -> None: ...

    def action_cycle_model(self) -> None: ...

    def action_toggle_tool_results(self) -> None: ...

    def action_toggle_thinking(self) -> None: ...

    def action_edit_queued_message(self) -> bool: ...

    async def action_submit_prompt(self) -> None: ...

    async def action_submit_follow_up(self) -> None: ...


class SessionCompletionRecord(Protocol):
    """Session metadata needed to render resume picker completions."""

    id: str
    title: str | None
    model: str
    cwd: Path
    updated_at: float


PASTE_DISPLAY_THRESHOLD = 2_000


class PromptInput(TextArea):
    """Multiline prompt input with completion key bindings."""

    BINDINGS: ClassVar[list[BindingEntry]] = []
    shell_mode_style: str = ""

    def __init__(
        self,
        *,
        tui_keybindings: TuiKeybindings | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("highlight_cursor_line", False)
        super().__init__(**kwargs)
        self.tui_keybindings = tui_keybindings or TuiKeybindings()
        self._base_bindings = self._bindings.copy()
        self._footer_mode: Literal["normal", "completion", "running"] = "normal"
        self._pending_pastes: list[tuple[str, str]] = []
        self._paste_placeholder_counter = 0
        self._apply_prompt_bindings()

    def set_footer_mode(self, mode: Literal["normal", "completion", "running"]) -> None:
        """Switch the prompt bindings shown by Textual's built-in footer."""
        if mode == self._footer_mode:
            return
        self._footer_mode = mode
        self._apply_prompt_bindings()
        self.refresh_bindings()

    def _apply_prompt_bindings(self) -> None:
        self._bindings = BindingsMap.merge(
            [
                self._base_bindings,
                BindingsMap(_prompt_bindings(self.tui_keybindings, mode=self._footer_mode)),
            ]
        )

    @property
    def value(self) -> str:
        """Compatibility alias for tests and code that previously used Input.value."""
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.text = text

    @property
    def cursor_position(self) -> int:
        """Return a flat cursor offset for Input compatibility."""
        row, column = self.cursor_location
        lines = self.text.split("\n")
        return sum(len(line) + 1 for line in lines[:row]) + column

    @cursor_position.setter
    def cursor_position(self, offset: int) -> None:
        text = self.text
        bounded = max(0, min(offset, len(text)))
        before = text[:bounded]
        self.move_cursor((before.count("\n"), len(before.rsplit("\n", 1)[-1])))

    def action_accept_completion(self) -> None:
        """Accept the selected app-level completion."""
        self._completion_target().action_accept_completion()

    def action_completion_next(self) -> None:
        """Select the next app-level completion or move down in the prompt."""
        if self._has_completion_options():
            self._completion_target().action_completion_next()
        else:
            self.action_cursor_down()

    def action_completion_previous(self) -> None:
        """Select the previous app-level completion or move up in the prompt."""
        if self._has_completion_options():
            self._completion_target().action_completion_previous()
        elif self._completion_target().action_edit_queued_message():
            return
        else:
            self.action_cursor_up()

    def action_cancel(self) -> None:
        """Run the app-level cancel action."""
        self._completion_target().action_cancel()

    def action_open_command_palette(self) -> None:
        """Open the app-level command palette."""
        self._completion_target().action_open_command_palette()

    def action_open_session_picker(self) -> None:
        """Open the app-level session picker."""
        self._completion_target().action_open_session_picker()

    def action_cycle_thinking(self) -> None:
        """Cycle the app-level thinking mode."""
        self._completion_target().action_cycle_thinking()

    def action_cycle_model(self) -> None:
        """Cycle the app-level scoped model."""
        self._completion_target().action_cycle_model()

    def action_toggle_tool_results(self) -> None:
        """Toggle app-level tool result display."""
        self._completion_target().action_toggle_tool_results()

    def action_toggle_thinking(self) -> None:
        """Toggle app-level thinking-token display."""
        self._completion_target().action_toggle_thinking()

    def action_clear_prompt(self) -> None:
        """Clear the current prompt."""
        if self.selected_text:
            return
        if self.text:
            self.text = ""
            self.move_cursor((0, 0))
            self._clear_pending_paste()

    def get_line(self, line_index: int) -> Text:
        """Retrieve one prompt line with shell prefixes highlighted."""
        line = super().get_line(line_index)
        if line_index != 0 or not self.shell_mode_style:
            return line
        span = _terminal_command_prefix_span(self.text)
        if span is None:
            return line
        start, end = span
        line.stylize(self.shell_mode_style, start, end)
        return line

    async def action_submit_follow_up(self) -> None:
        """Submit the prompt as an app-level follow-up."""
        await self._completion_target().action_submit_follow_up()

    async def action_submit_prompt(self) -> None:
        """Submit the prompt through the app-level action."""
        await self._completion_target().action_submit_prompt()

    def action_insert_newline(self) -> None:
        """Insert a newline in the prompt."""
        self.insert("\n")

    async def action_quit(self) -> None:
        """Quit the app through the app-level action."""
        await self.app.action_quit()

    def action_scroll_down(self) -> None:
        """Use down arrow for completion selection while focused."""
        self.action_completion_next()

    def action_scroll_up(self) -> None:
        """Use up arrow for completion selection while focused."""
        self.action_completion_previous()

    def on_paste(self, event: events.Paste) -> None:
        """Show a compact placeholder instead of rendering very large pasted text."""
        if len(event.text) <= PASTE_DISPLAY_THRESHOLD:
            return
        event.stop()
        event.prevent_default()
        self._show_large_paste_placeholder(event.text)

    def _show_large_paste_placeholder(self, content: str) -> None:
        """Store large pasted text and render a compact placeholder."""
        self._paste_placeholder_counter += 1
        placeholder = self._large_paste_placeholder(content, self._paste_placeholder_counter)
        self._pending_pastes.append((placeholder, content))
        self.insert(placeholder)

    def _large_paste_placeholder(self, content: str, paste_number: int) -> str:
        """Build the display text for a large paste."""
        char_count = len(content)
        line_count = content.count("\n") + 1
        kb = char_count / 1024
        parts: list[str] = [f"{char_count:,} characters"]
        if line_count > 1:
            parts.append(f"{line_count} lines")
        if kb >= 1:
            parts.append(f"{kb:.1f} KB")
        return f"[Pasted content #{paste_number}: {', '.join(parts)}]"

    def _clear_pending_paste(self) -> None:
        """Forget any stored large paste content."""
        self._pending_pastes.clear()

    def sync_pending_paste(self) -> None:
        """Invalidate stored paste content when its placeholder is edited away."""
        self._pending_pastes = [
            (placeholder, content)
            for placeholder, content in self._pending_pastes
            if placeholder in self.text
        ]

    def text_for_submission(self) -> str:
        """Return the prompt text, expanding intact large-paste placeholders."""
        self.sync_pending_paste()
        text = self.text
        for placeholder, content in self._pending_pastes:
            text = text.replace(placeholder, content, 1)
        return text

    async def on_key(self, event: Key) -> None:
        """Route completion and submission keys before default input handling.

        Extension key interceptors are consulted upstream in
        :meth:`TauTuiApp.on_event` (pre-dispatch, before app-level priority
        bindings), so there is no interceptor splice here.
        """
        keybindings = self.tui_keybindings
        if event.key == keybindings.queue_follow_up:
            event.stop()
            event.prevent_default()
            await self._completion_target().action_submit_follow_up()
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            await self._completion_target().action_submit_prompt()
        elif event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
        elif event.key == keybindings.accept_completion:
            event.stop()
            self._completion_target().action_accept_completion()
        elif event.key == keybindings.cancel:
            event.stop()
            self._completion_target().action_cancel()
        elif event.key == keybindings.command_palette:
            event.stop()
            self._completion_target().action_open_command_palette()
        elif event.key == keybindings.session_picker:
            event.stop()
            self._completion_target().action_open_session_picker()
        elif _is_thinking_cycle_key(event.key, keybindings.thinking_cycle):
            event.stop()
            self._completion_target().action_cycle_thinking()
        elif event.key == keybindings.model_cycle:
            event.stop()
            self._completion_target().action_cycle_model()
        elif event.key == keybindings.toggle_tool_results:
            event.stop()
            self._completion_target().action_toggle_tool_results()
        elif event.key == keybindings.toggle_thinking:
            event.stop()
            self._completion_target().action_toggle_thinking()
        elif event.key == keybindings.copy_message:
            if self.selected_text:
                return
            event.stop()
            event.prevent_default()
            if self.text:
                self.text = ""
                self.move_cursor((0, 0))
        elif event.key == keybindings.completion_next:
            event.stop()
            if self._has_completion_options():
                self._completion_target().action_completion_next()
            else:
                self.action_cursor_down()
        elif event.key == keybindings.completion_previous:
            event.stop()
            self.action_completion_previous()
        elif event.key == keybindings.quit:
            event.stop()
            await self.action_quit()

    def _has_completion_options(self) -> bool:
        completion_state = getattr(self.app, "_completion_state", None)
        return bool(getattr(completion_state, "items", ()))

    def _completion_target(self) -> CompletionActionTarget:
        return cast(CompletionActionTarget, self.app)


class ExtensionSelectScreen(ModalScreen[str | None]):
    """Modal option picker backing `context.ui.select`.

    Binding/key wiring mirrors `SessionPickerScreen`. Note: the app binds
    Up/Down globally with priority (completion navigation), so this screen
    must also be listed in the `action_completion_next/previous` and
    `action_accept_completion` screen allowlists for arrow keys to reach
    the option list.
    """

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
    ]

    def __init__(
        self,
        title: str,
        options: Sequence[str],
        *,
        theme: TuiTheme,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.options = tuple(options)
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the option picker."""
        with Vertical(id="extension-select"):
            yield Static(self.title_text, id="extension-select-title", markup=False)
            yield ListView(
                *[ListItem(Label(option, markup=False)) for option in self.options],
                id="extension-select-list",
            )
            yield Static("Enter selects - Escape cancels", id="extension-select-help")

    def on_mount(self) -> None:
        """Focus the option list for keyboard navigation."""
        option_list = self.query_one("#extension-select-list", ListView)
        option_list.index = 0
        option_list.focus()

    def on_key(self, event: Key) -> None:
        """Route arrow and enter keys to the option list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the chosen option."""
        self.dismiss(self.options[event.index])

    def action_cursor_up(self) -> None:
        """Move to the previous option."""
        self.query_one("#extension-select-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next option."""
        self.query_one("#extension-select-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Select the highlighted option."""
        self.query_one("#extension-select-list", ListView).action_select_cursor()

    def action_cancel(self) -> None:
        """Close without choosing an option."""
        self.dismiss(None)


class ExtensionConfirmScreen(ModalScreen[bool]):
    """Modal yes/no confirmation backing `context.ui.confirm`.

    Binding/key wiring mirrors `SessionPickerScreen`; see
    `ExtensionSelectScreen` for the app-level Up/Down allowlist requirement.
    """

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
    ]

    def __init__(self, title: str, message: str, *, theme: TuiTheme) -> None:
        super().__init__()
        self.title_text = title
        self.message = message
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the confirmation dialog."""
        with Vertical(id="extension-confirm"):
            yield Static(self.title_text, id="extension-confirm-title", markup=False)
            yield Static(self.message, id="extension-confirm-message", markup=False)
            yield ListView(
                ListItem(Label("Yes", markup=False)),
                ListItem(Label("No", markup=False)),
                id="extension-confirm-list",
            )
            yield Static("Enter selects - Escape cancels", id="extension-confirm-help")

    def on_mount(self) -> None:
        """Focus the choice list."""
        choice_list = self.query_one("#extension-confirm-list", ListView)
        choice_list.index = 0
        choice_list.focus()

    def on_key(self, event: Key) -> None:
        """Route arrow and enter keys to the choice list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the confirmation result (Yes is index 0)."""
        self.dismiss(event.index == 0)

    def action_cursor_up(self) -> None:
        """Move to the previous choice."""
        self.query_one("#extension-confirm-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next choice."""
        self.query_one("#extension-confirm-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Select the highlighted choice."""
        self.query_one("#extension-confirm-list", ListView).action_select_cursor()

    def action_cancel(self) -> None:
        """Close, declining the confirmation."""
        self.dismiss(False)


class ExtensionInputScreen(ModalScreen[str | None]):
    """Modal single-line text prompt backing `context.ui.input`."""

    BINDINGS: ClassVar[list[BindingEntry]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, placeholder: str = "", *, theme: TuiTheme) -> None:
        super().__init__()
        self.title_text = title
        self.placeholder = placeholder
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the text prompt."""
        with Vertical(id="extension-input"):
            yield Static(self.title_text, id="extension-input-title", markup=False)
            yield Input(placeholder=self.placeholder, id="extension-input-field")
            yield Static("Enter submits - Escape cancels", id="extension-input-help")

    def on_mount(self) -> None:
        """Focus the text field."""
        self.query_one("#extension-input-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Dismiss with the entered text."""
        if event.input.id != "extension-input-field":
            return
        event.stop()
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        """Close without submitting text."""
        self.dismiss(None)


class SessionPickerSearchInput(Input):
    """Search input that keeps session-picker navigation local to the picker."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
    ]

    def _picker(self) -> SessionPickerScreen:
        return cast(SessionPickerScreen, self.screen)

    def on_key(self, event: Key) -> None:
        """Route picker control keys before the input edits its text."""
        if event.key == "up":
            event.stop()
            event.prevent_default()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            event.prevent_default()
            self.action_cursor_down()
        elif event.key == "escape":
            event.stop()
            event.prevent_default()
            self.action_cancel()

    def action_cursor_up(self) -> None:
        """Move the session picker selection up."""
        self._picker().action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move the session picker selection down."""
        self._picker().action_cursor_down()

    def action_cancel(self) -> None:
        """Close the session picker."""
        self._picker().action_cancel()


class SessionPickerScreen(ModalScreen[str | None]):
    """Minimal modal picker for indexed sessions, with a search field."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
    ]

    def __init__(
        self,
        records: Sequence[SessionCompletionRecord],
        *,
        theme: TuiTheme,
    ) -> None:
        super().__init__()
        self.records = tuple(records)
        self.visible_records = self.records
        self.theme = theme
        self.search_value = ""

    def compose(self) -> ComposeResult:
        """Compose the session picker."""
        with Vertical(id="session-picker"):
            yield Static("Sessions", id="session-picker-title")
            yield SessionPickerSearchInput(
                placeholder="Search sessions",
                id="session-picker-search",
            )
            yield ListView(
                *[
                    ListItem(Label(_session_picker_label(record), markup=False))
                    for record in self.records
                ],
                id="session-picker-list",
            )
            yield Static("Enter selects - Escape closes", id="session-picker-help")

    def on_mount(self) -> None:
        """Focus the search field for keyboard navigation."""
        search = self.query_one("#session-picker-search", Input)
        search.focus()
        self._refresh_session_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter session choices as the search value changes."""
        if event.input.id != "session-picker-search":
            return
        event.stop()
        self.search_value = event.value
        self._refresh_session_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Select the highlighted session from the search field."""
        if event.input.id != "session-picker-search":
            return
        event.stop()
        self._select_visible_record()

    def on_key(self, event: Key) -> None:
        """Route session picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected session id."""
        event.stop()
        self._select_visible_record()

    def action_cursor_up(self) -> None:
        """Move to the previous session."""
        self.query_one("#session-picker-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next session."""
        self.query_one("#session-picker-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Select the highlighted session."""
        self._select_visible_record()

    def action_cancel(self) -> None:
        """Close the picker without selecting a session."""
        self.dismiss(None)

    def _select_visible_record(self) -> None:
        if not self.visible_records:
            return
        session_list = self.query_one("#session-picker-list", ListView)
        index = session_list.index
        if index is None:
            return
        self.dismiss(self.visible_records[index].id)

    def _refresh_session_list(self) -> None:
        self.visible_records = _filter_session_records(self.records, self.search_value)
        session_list = self.query_one("#session-picker-list", ListView)
        session_list.clear()
        session_list.extend(
            [
                ListItem(Label(_session_picker_label(record), markup=False))
                for record in self.visible_records
            ]
        )
        session_list.index = 0 if self.visible_records else None
        help_text = (
            "Enter selects - Escape closes"
            if self.visible_records
            else "No matching sessions - Escape closes"
        )
        self.query_one("#session-picker-help", Static).update(help_text)


@dataclass(frozen=True, slots=True)
class TreePickerResult:
    """Tree-picker branch selection."""

    entry_id: str
    summarize: bool = False
    custom_instructions: str | None = None


class _TreePickerListItem(ListItem):
    """Tree entry that keeps inline label colors readable when highlighted."""

    def __init__(self, choice: SessionTreeChoice, *, theme: TuiTheme) -> None:
        self.choice = choice
        self.theme = theme
        super().__init__(Label(_tree_picker_label(choice, theme=theme), markup=False))

    def watch_highlighted(self, value: bool) -> None:
        """Recolor inline label spans when the list highlight changes."""
        super().watch_highlighted(value)
        self.query_one(Label).update(
            _tree_picker_label(self.choice, theme=self.theme, highlighted=value)
        )


class TreePickerScreen(ModalScreen[TreePickerResult | None]):
    """Modal picker for branching from a previous session entry."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Branch", show=False),
        Binding("s", "select_with_summary", "Summarize", show=False),
        Binding("c", "select_with_custom_summary", "Custom summary", show=False),
        Binding("ctrl+t", "toggle_tool_calls", "Tool calls", show=False),
    ]

    def __init__(
        self,
        choices: Sequence[SessionTreeChoice],
        *,
        theme: TuiTheme,
    ) -> None:
        super().__init__()
        self.choices = tuple(choices)
        self.theme = theme
        self.show_tool_calls = True

    def compose(self) -> ComposeResult:
        """Compose the tree picker."""
        with Vertical(id="tree-picker"):
            yield Static("Session Tree", id="tree-picker-title")
            yield ListView(
                *self._list_items(),
                id="tree-picker-list",
            )
            yield Static(
                self._help_text(),
                id="tree-picker-help",
            )

    def on_mount(self) -> None:
        """Focus the tree list for keyboard navigation."""
        tree_list = self.query_one("#tree-picker-list", ListView)
        tree_list.index = _active_tree_choice_index(self.choices)
        tree_list.focus()

    def on_key(self, event: Key) -> None:
        """Route tree picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()
        elif event.key == "s":
            event.stop()
            self.action_select_with_summary()
        elif event.key == "c":
            event.stop()
            self.action_select_with_custom_summary()
        elif event.key == "ctrl+t":
            event.stop()
            self.action_toggle_tool_calls()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected entry id."""
        self.dismiss(TreePickerResult(entry_id=self._visible_choices()[event.index].entry_id))

    def action_cursor_up(self) -> None:
        """Move to the previous tree entry."""
        self.query_one("#tree-picker-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next tree entry."""
        self.query_one("#tree-picker-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Branch from the highlighted entry without a summary."""
        self.query_one("#tree-picker-list", ListView).action_select_cursor()

    def action_select_with_summary(self) -> None:
        """Branch from the highlighted entry with a branch summary."""
        tree_list = self.query_one("#tree-picker-list", ListView)
        index = tree_list.index
        if index is None:
            return
        self.dismiss(
            TreePickerResult(entry_id=self._visible_choices()[index].entry_id, summarize=True)
        )

    def action_select_with_custom_summary(self) -> None:
        """Branch from the highlighted entry with custom summary instructions."""
        tree_list = self.query_one("#tree-picker-list", ListView)
        index = tree_list.index
        if index is None:
            return
        self.app.push_screen(
            BranchSummaryInstructionsScreen(theme=self.theme),
            callback=lambda instructions: self._dismiss_with_custom_summary(index, instructions),
        )

    def _dismiss_with_custom_summary(self, index: int, instructions: str | None) -> None:
        if instructions is None:
            return
        visible_choices = self._visible_choices()
        if index >= len(visible_choices):
            return
        self.dismiss(
            TreePickerResult(
                entry_id=visible_choices[index].entry_id,
                summarize=True,
                custom_instructions=instructions,
            )
        )

    def action_toggle_tool_calls(self) -> None:
        """Toggle tool-call entries in the tree picker."""
        self.run_worker(self._toggle_tool_calls())

    async def _toggle_tool_calls(self) -> None:
        selected_entry_id = self._selected_entry_id()
        self.show_tool_calls = not self.show_tool_calls
        tree_list = self.query_one("#tree-picker-list", ListView)
        await tree_list.clear()
        await tree_list.extend(self._list_items())
        visible_choices = self._visible_choices()
        tree_list.index = _tree_choice_index(visible_choices, selected_entry_id)
        self.query_one("#tree-picker-help", Static).update(self._help_text())

    def _selected_entry_id(self) -> str | None:
        tree_list = self.query_one("#tree-picker-list", ListView)
        index = tree_list.index
        visible_choices = self._visible_choices()
        if index is None or index >= len(visible_choices):
            return None
        return visible_choices[index].entry_id

    def _visible_choices(self) -> tuple[SessionTreeChoice, ...]:
        if self.show_tool_calls:
            return self.choices
        return tuple(choice for choice in self.choices if not choice.is_tool_call)

    def _list_items(self) -> list[ListItem]:
        return [_TreePickerListItem(choice, theme=self.theme) for choice in self._visible_choices()]

    def _help_text(self) -> str:
        tool_call_state = "shown" if self.show_tool_calls else "hidden"
        return (
            "Enter branches - S summarizes - C custom summary - "
            f"Ctrl+T tool calls {tool_call_state} - Escape closes"
        )

    def action_cancel(self) -> None:
        """Close the picker without selecting an entry."""
        self.dismiss(None)


class BranchSummaryInstructionsScreen(ModalScreen[str | None]):
    """Prompt for custom branch-summary instructions."""

    BINDINGS: ClassVar[list[BindingEntry]] = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, *, theme: TuiTheme) -> None:
        super().__init__()
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the custom-instructions prompt."""
        with Vertical(id="branch-summary-instructions"):
            yield Static(
                "Custom summarization instructions",
                id="branch-summary-instructions-title",
            )
            yield TextArea(id="branch-summary-instructions-input")
            yield Static(
                "Ctrl+Enter submits - Escape returns to tree",
                id="branch-summary-instructions-help",
            )

    def on_mount(self) -> None:
        """Focus the instruction editor."""
        self.query_one("#branch-summary-instructions-input", TextArea).focus()

    def on_key(self, event: Key) -> None:
        """Submit on Ctrl+Enter and cancel on Escape."""
        if event.key == "ctrl+enter":
            event.stop()
            self.action_submit()
        elif event.key == "escape":
            event.stop()
            self.action_cancel()

    def action_submit(self) -> None:
        """Submit custom instructions."""
        value = self.query_one("#branch-summary-instructions-input", TextArea).text.strip()
        self.dismiss(value or None)

    def action_cancel(self) -> None:
        """Cancel custom instructions."""
        self.dismiss(None)


class CommandOutputScroll(VerticalScroll):
    """Scrollable command output area with deterministic arrow-key scrolling."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("up", "scroll_up", "Scroll up", show=False, priority=True),
        Binding("down", "scroll_down", "Scroll down", show=False, priority=True),
    ]

    def action_scroll_up(self) -> None:
        """Scroll command output up."""
        self.scroll_y = max(0, self.scroll_y - 1)

    def action_scroll_down(self) -> None:
        """Scroll command output down."""
        self.scroll_y = min(self.max_scroll_y, self.scroll_y + 1)


class CommandOutputScreen(ModalScreen[None]):
    """Dismissible modal for slash-command output."""

    auto_copy_selection: bool = False

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
        Binding("up", "scroll_up", "Scroll up", show=False, priority=True),
        Binding("down", "scroll_down", "Scroll down", show=False, priority=True),
    ]

    def __init__(
        self,
        title: str,
        message: str,
        *,
        theme: TuiTheme,
        auto_copy_selection: bool = False,
    ) -> None:
        super().__init__()
        self.title_text = title
        self.message = message
        self.theme = theme
        self.auto_copy_selection = auto_copy_selection

    def compose(self) -> ComposeResult:
        """Compose command output."""
        with Vertical(id="command-output"):
            yield Static(self.title_text, id="command-output-title")
            with CommandOutputScroll(id="command-output-scroll"):
                yield Static(self.message, id="command-output-body", markup=False)
            yield Static(self._help_text(), id="command-output-help")

    def on_mount(self) -> None:
        """Focus the scroll area so arrow keys navigate long output."""
        self.query_one("#command-output-scroll", VerticalScroll).focus()

    def on_key(self, event: Key) -> None:
        """Route arrow keys to the command output scroll area."""
        if event.key == "up":
            event.stop()
            self.action_scroll_up()
        elif event.key == "down":
            event.stop()
            self.action_scroll_down()

    def action_close(self) -> None:
        """Close the command output modal."""
        self.dismiss(None)

    def _help_text(self) -> str:
        if self.auto_copy_selection:
            return "Select text to copy - Enter or Escape closes"
        return "Enter or Escape closes"

    def action_scroll_up(self) -> None:
        """Scroll command output up."""
        self.query_one("#command-output-scroll", CommandOutputScroll).action_scroll_up()

    def action_scroll_down(self) -> None:
        """Scroll command output down."""
        self.query_one("#command-output-scroll", CommandOutputScroll).action_scroll_down()


class LoginProviderSearchInput(Input):
    """Search input that keeps provider-picker navigation local."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
    ]

    def _picker(self) -> LoginProviderPickerScreen:
        return cast(LoginProviderPickerScreen, self.screen)

    def on_key(self, event: Key) -> None:
        """Route picker control keys before the input edits its text."""
        if event.key == "up":
            event.stop()
            event.prevent_default()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            event.prevent_default()
            self.action_cursor_down()
        elif event.key == "escape":
            event.stop()
            event.prevent_default()
            self.action_cancel()

    def action_cursor_up(self) -> None:
        """Move the provider picker selection up."""
        self._picker().action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move the provider picker selection down."""
        self._picker().action_cursor_down()

    def action_cancel(self) -> None:
        """Close the provider picker."""
        self._picker().action_cancel()


class _LoginFlowAction(Enum):
    """Navigation actions returned by nested login screens."""

    BACK = auto()


class LoginProviderPickerScreen(ModalScreen[str | _LoginFlowAction | None]):
    """Searchable provider picker for the TUI login flow."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+d", "close", "Close", priority=True),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
    ]

    def __init__(
        self,
        providers: Sequence[ProviderCatalogEntry],
        *,
        theme: TuiTheme,
        title: str = "Login",
        back_on_cancel: bool = False,
    ) -> None:
        super().__init__()
        self.providers = tuple(providers)
        self.back_on_cancel = back_on_cancel
        self.visible_providers = self.providers
        self.theme = theme
        self.title_text = title

    def compose(self) -> ComposeResult:
        """Compose the provider picker."""
        with Vertical(id="login-provider-picker"):
            yield Static(self.title_text, id="login-provider-title")
            yield LoginProviderSearchInput(
                placeholder="Search providers",
                id="login-provider-search",
            )
            yield ListView(
                *[
                    ListItem(Label(_login_provider_label(provider), markup=False))
                    for provider in self.providers
                ],
                id="login-provider-list",
            )
            yield Static("Enter selects - Escape closes", id="login-provider-help")

    def on_mount(self) -> None:
        """Focus the provider search field."""
        self.query_one("#login-provider-search", Input).focus()
        self._refresh_provider_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter providers as the search value changes."""
        if event.input.id != "login-provider-search":
            return
        event.stop()
        self.visible_providers = _filter_login_providers(self.providers, event.value)
        self._refresh_provider_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Select the highlighted provider from the search field."""
        if event.input.id != "login-provider-search":
            return
        event.stop()
        self._select_visible_provider()

    def on_key(self, event: Key) -> None:
        """Route provider picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected provider name."""
        event.stop()
        self._select_visible_provider()

    def action_cursor_up(self) -> None:
        """Move to the previous provider."""
        self.query_one("#login-provider-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next provider."""
        self.query_one("#login-provider-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Select the highlighted provider."""
        self._select_visible_provider()

    def action_cancel(self) -> None:
        """Go back in a login flow, or close a standalone provider picker."""
        self.dismiss(_LoginFlowAction.BACK if self.back_on_cancel else None)

    def action_close(self) -> None:
        """Close the entire login flow."""
        self.dismiss(None)

    def _select_visible_provider(self) -> None:
        provider_list = self.query_one("#login-provider-list", ListView)
        index = provider_list.index
        if index is None or not self.visible_providers:
            return
        self.dismiss(self.visible_providers[index].name)

    def _refresh_provider_list(self) -> None:
        provider_list = self.query_one("#login-provider-list", ListView)
        provider_list.clear()
        provider_list.extend(
            [
                ListItem(Label(_login_provider_label(provider), markup=False))
                for provider in self.visible_providers
            ]
        )
        provider_list.index = 0 if self.visible_providers else None
        help_text = (
            "Enter selects - Escape closes"
            if self.visible_providers
            else "No matching providers - Escape closes"
        )
        self.query_one("#login-provider-help", Static).update(help_text)


@dataclass(frozen=True, slots=True)
class CustomProviderLoginResult:
    """Provider details collected by the custom-provider login flow."""

    provider_name: str
    display_name: str
    base_url: str
    api_key_env: str
    models: tuple[str, ...]
    default_model: str
    api_key: str


class LoginMethodPickerScreen(ModalScreen[str | None]):
    """Login method picker for the TUI login flow."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("ctrl+d", "cancel", "Close", priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select_cursor", "Select", show=False, priority=True),
    ]

    def __init__(self, *, theme: TuiTheme) -> None:
        super().__init__()
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the login method picker."""
        with Vertical(id="login-method-picker"):
            yield Static("Login", id="login-method-title")
            yield Static("Choose how to authenticate.", id="login-method-intro")
            yield LoginMethodListView(
                ListItem(
                    Label("Subscription — OAuth account", markup=False),
                    id="login-method-subscription",
                ),
                ListItem(
                    Label("API key — built-in provider", markup=False),
                    id="login-method-api-key",
                ),
                ListItem(
                    Label("Custom provider — OpenAI-compatible", markup=False),
                    id="login-method-custom",
                ),
                id="login-method-list",
            )
            yield Static("Enter selects - Escape/Ctrl+D closes", id="login-method-help")

    def on_mount(self) -> None:
        """Focus the default subscription method."""
        method_list = self.query_one("#login-method-list", ListView)
        method_list.index = 0
        method_list.focus()

    def on_key(self, event: Key) -> None:
        """Route arrow keys between login method buttons."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss with the selected login method."""
        if event.button.id == "login-method-subscription":
            self.dismiss("subscription")
        elif event.button.id == "login-method-api-key":
            self.dismiss("api-key")
        elif event.button.id == "login-method-custom":
            self.dismiss("custom")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected login method."""
        if event.item.id == "login-method-subscription":
            self.dismiss("subscription")
        elif event.item.id == "login-method-api-key":
            self.dismiss("api-key")
        elif event.item.id == "login-method-custom":
            self.dismiss("custom")

    def action_cancel(self) -> None:
        """Close without selecting a login method."""
        self.dismiss(None)

    def action_cursor_up(self) -> None:
        """Focus the previous login method."""
        self._move_method_cursor(offset=-1)

    def action_cursor_down(self) -> None:
        """Focus the next login method."""
        self._move_method_cursor(offset=1)

    def action_select_cursor(self) -> None:
        """Select the currently focused login method."""
        self.query_one("#login-method-list", ListView).action_select_cursor()

    def _move_method_cursor(self, *, offset: int) -> None:
        method_list = self.query_one("#login-method-list", ListView)
        item_count = len(method_list.children)
        if item_count == 0:
            method_list.index = None
            return
        current_index = method_list.index if method_list.index is not None else 0
        method_list.index = (current_index + offset) % item_count


class LoginMethodListView(ListView):
    """List view with wrapping arrow navigation for the login method picker."""

    def action_cursor_up(self) -> None:
        """Move to the previous login method."""
        self._move_cursor(offset=-1)

    def action_cursor_down(self) -> None:
        """Move to the next login method."""
        self._move_cursor(offset=1)

    def _move_cursor(self, *, offset: int) -> None:
        item_count = len(self.children)
        if item_count == 0:
            self.index = None
            return
        current_index = self.index if self.index is not None else 0
        self.index = (current_index + offset) % item_count


class ThemePickerScreen(ModalScreen[TuiThemeName | None]):
    """Theme picker for the available TUI themes."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select_cursor", "Select", show=False, priority=True),
    ]

    def __init__(
        self,
        *,
        current_theme: TuiThemeName,
        theme: TuiTheme,
        theme_names: tuple[TuiThemeName, ...],
    ) -> None:
        super().__init__()
        self.current_theme = current_theme
        self.theme = theme
        self.theme_names = theme_names

    def compose(self) -> ComposeResult:
        """Compose the theme picker."""
        with Vertical(id="theme-picker"):
            yield Static("Theme", id="theme-picker-title")
            yield ListView(
                *[
                    ListItem(
                        Label(
                            _theme_picker_label(theme_name, current_theme=self.current_theme),
                            markup=False,
                        )
                    )
                    for theme_name in self.theme_names
                ],
                id="theme-picker-list",
            )
            yield Static("Enter selects - Escape closes", id="theme-picker-help")

    def on_mount(self) -> None:
        """Select the current theme."""
        theme_list = self.query_one("#theme-picker-list", ListView)
        try:
            theme_list.index = self.theme_names.index(self.current_theme)
        except ValueError:
            theme_list.index = 0
        theme_list.focus()

    def on_key(self, event: Key) -> None:
        """Route theme picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_select_cursor()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected theme name."""
        self.dismiss(self.theme_names[event.index])

    def action_cursor_up(self) -> None:
        """Move to the previous theme."""
        self.query_one("#theme-picker-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next theme."""
        self.query_one("#theme-picker-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Select the highlighted theme."""
        self.query_one("#theme-picker-list", ListView).action_select_cursor()

    def action_cancel(self) -> None:
        """Close without selecting a theme."""
        self.dismiss(None)


class ModelPickerSearchInput(Input):
    """Search input that keeps model-picker control keys local to the picker."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("tab", "toggle_mode", "Mode", show=False, priority=True),
        Binding("ctrl+i", "toggle_mode", "Mode", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
    ]

    def _picker(self) -> ModelPickerScreen:
        return cast(ModelPickerScreen, self.screen)

    def on_key(self, event: Key) -> None:
        """Route picker control keys before the input edits its text."""
        if event.key == "up":
            event.stop()
            event.prevent_default()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            event.prevent_default()
            self.action_cursor_down()
        elif event.key in {"tab", "ctrl+i"}:
            event.stop()
            event.prevent_default()
            self.action_toggle_mode()
        elif event.key == "escape":
            event.stop()
            event.prevent_default()
            self.action_cancel()

    def action_cursor_up(self) -> None:
        """Move the model picker selection up."""
        self._picker().action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move the model picker selection down."""
        self._picker().action_cursor_down()

    def action_toggle_mode(self) -> None:
        """Toggle between all and scoped picker modes."""
        self._picker().action_toggle_mode()

    def action_cancel(self) -> None:
        """Close the model picker."""
        self._picker().action_cancel()


class ModelPickerScreen(ModalScreen[ModelChoice | None]):
    """Model picker for the active TUI provider."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("tab", "toggle_mode", "Mode", show=False, priority=True),
        Binding("ctrl+i", "toggle_mode", "Mode", show=False, priority=True),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "accept_model", "Select", show=False),
    ]

    def __init__(
        self,
        choices: Sequence[ModelChoice],
        *,
        scoped_choices: Sequence[ModelChoice],
        current_model: str,
        provider_name: str,
        theme: TuiTheme,
        on_toggle_scoped: Callable[[ModelChoice], Sequence[ModelChoice]] | None = None,
        picker_kind: Literal["model", "scoped"] = "model",
    ) -> None:
        super().__init__()
        self.choices = tuple(dict.fromkeys(choices))
        self.scoped_choices = tuple(dict.fromkeys(scoped_choices))
        self.visible_choices = self.choices
        self.current_model = current_model
        self.provider_name = provider_name
        self.theme = theme
        self.on_toggle_scoped = on_toggle_scoped
        self.picker_kind = picker_kind
        self.mode: Literal["all", "scoped"] = "all"
        self.search_value = ""

    def compose(self) -> ComposeResult:
        """Compose the model picker."""
        with Vertical(id="model-picker"):
            title = (
                f"Model: {self.provider_name}" if self.picker_kind == "model" else "Scoped models"
            )
            yield Static(title, id="model-picker-title")
            yield Static("", id="model-picker-tabs")
            yield ModelPickerSearchInput(placeholder="Search models", id="model-picker-search")
            yield ListView(
                *[
                    ListItem(
                        Label(
                            _model_picker_label(
                                choice,
                                current_model=self.current_model,
                                current_provider=self.provider_name,
                                scoped=choice in self.scoped_choices,
                            ),
                            markup=False,
                        )
                    )
                    for choice in self.choices
                ],
                id="model-picker-list",
            )
            yield Static("", id="model-picker-help")

    def on_mount(self) -> None:
        """Focus the search field."""
        search = self.query_one("#model-picker-search", Input)
        search.focus()
        self._refresh_model_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter model choices as the search value changes."""
        if event.input.id != "model-picker-search":
            return
        event.stop()
        self.search_value = event.value
        self._refresh_model_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Select the highlighted model from the search field."""
        if event.input.id != "model-picker-search":
            return
        event.stop()
        self._select_visible_choice()

    def _reset_model_list_index(self) -> None:
        """Move selection to the current model or first visible row."""
        model_list = self.query_one("#model-picker-list", ListView)
        if not self.visible_choices:
            model_list.index = None
            return
        try:
            model_list.index = self.visible_choices.index(
                ModelChoice(provider_name=self.provider_name, model=self.current_model)
            )
        except ValueError:
            model_list.index = 0

    def on_key(self, event: Key) -> None:
        """Route model picker keys to the list."""
        if event.key == "up":
            event.stop()
            self.action_cursor_up()
        elif event.key == "down":
            event.stop()
            self.action_cursor_down()
        elif event.key == "enter":
            event.stop()
            self.action_accept_model()
        elif event.key in {"tab", "ctrl+i"}:
            event.stop()
            self.action_toggle_mode()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle the selected row."""
        event.stop()
        self._select_visible_choice()

    def action_cursor_up(self) -> None:
        """Move to the previous model."""
        self.query_one("#model-picker-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next model."""
        self.query_one("#model-picker-list", ListView).action_cursor_down()

    def action_accept_model(self) -> None:
        """Select the highlighted model."""
        self._select_visible_choice()

    def action_toggle_mode(self) -> None:
        """Toggle between all models and scoped models."""
        if self.picker_kind != "model":
            return
        self.mode = "scoped" if self.mode == "all" else "all"
        self._refresh_model_list()

    def action_toggle_scoped(self) -> None:
        """Add or remove the highlighted model from scoped models."""
        if self.on_toggle_scoped is None or not self.visible_choices:
            return
        model_list = self.query_one("#model-picker-list", ListView)
        index = model_list.index
        if index is None:
            return
        choice = self.visible_choices[index]
        self.scoped_choices = tuple(dict.fromkeys(self.on_toggle_scoped(choice)))
        self._refresh_model_list()

    def action_cancel(self) -> None:
        """Close without selecting a model."""
        self.dismiss(None)

    def _select_visible_choice(self) -> None:
        if not self.visible_choices:
            return
        model_list = self.query_one("#model-picker-list", ListView)
        index = model_list.index
        if index is None:
            return
        choice = self.visible_choices[index]
        if self.picker_kind == "scoped":
            self.action_toggle_scoped()
            return
        self.dismiss(choice)

    def _refresh_model_list(self) -> None:
        base_choices = self.scoped_choices if self.mode == "scoped" else self.choices
        self.visible_choices = _filter_model_choices(base_choices, self.search_value)
        model_list = self.query_one("#model-picker-list", ListView)
        model_list.clear()
        model_list.extend(
            [
                ListItem(
                    Label(
                        _model_picker_label(
                            choice,
                            current_model=self.current_model,
                            current_provider=self.provider_name,
                            scoped=choice in self.scoped_choices,
                        ),
                        markup=False,
                    )
                )
                for choice in self.visible_choices
            ]
        )
        self._reset_model_list_index()
        scope_count = len(self.scoped_choices)
        tabs = self.query_one("#model-picker-tabs", Static)
        if self.picker_kind == "scoped":
            tabs.update("Scoped models setup — Enter toggles membership; active model is unchanged")
            help_text = (
                "No matching models - Enter toggles scoped model"
                if not self.visible_choices
                else f"Enter toggles scoped model - {scope_count} scoped"
            )
        elif self.mode == "all":
            tabs.update("Tabs: ● All models  ○ Scoped models")
            help_text = (
                "all models: no matching models - Tab switches to scoped models"
                if not self.visible_choices
                else (
                    "All models - Enter selects active model - Tab switches tabs - "
                    f"{scope_count} scoped"
                )
            )
        else:
            tabs.update("Tabs: ○ All models  ● Scoped models")
            help_text = (
                "scoped models: no matching models - Tab switches to all models"
                if not self.visible_choices
                else "Scoped models - Enter selects active model - Tab switches tabs"
            )
        self.query_one("#model-picker-help", Static).update(help_text)


class CustomProviderLoginScreen(ModalScreen[CustomProviderLoginResult | _LoginFlowAction | None]):
    """Prompt for adding an OpenAI-compatible custom provider."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+d", "close", "Close", priority=True),
    ]

    _INPUT_ORDER: ClassVar[tuple[str, ...]] = (
        "custom-provider-name",
        "custom-provider-display-name",
        "custom-provider-base-url",
        "custom-provider-api-key-env",
        "custom-provider-models",
        "custom-provider-default-model",
        "custom-provider-api-key",
    )

    def __init__(self, *, theme: TuiTheme) -> None:
        super().__init__()
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the custom provider prompt."""
        with Vertical(id="login-screen"):
            yield Static("Add custom provider", id="login-title")
            yield Static(
                "Short provider name is used in commands/config.",
                id="custom-provider-help",
            )
            yield Input(placeholder="Provider name/id, e.g. nebius", id="custom-provider-name")
            yield Input(
                placeholder="Display name shown in UI, e.g. Nebius AI Studio",
                id="custom-provider-display-name",
            )
            yield Input(
                placeholder="OpenAI-compatible base URL, e.g. https://api.studio.nebius.ai/v1",
                id="custom-provider-base-url",
            )
            yield Input(
                placeholder="API key environment variable fallback, e.g. NEBIUS_API_KEY",
                id="custom-provider-api-key-env",
            )
            yield Input(
                placeholder="Model ids, comma-separated, e.g. model-a, model-b",
                id="custom-provider-models",
            )
            yield Input(
                placeholder="Default model id, must be listed above",
                id="custom-provider-default-model",
            )
            yield Input(
                placeholder="Paste API key to save for this provider",
                password=True,
                id="custom-provider-api-key",
            )
            yield Static(
                "Enter advances/saves - Escape goes back - Ctrl+D closes",
                id="login-footer",
            )

    def on_mount(self) -> None:
        """Focus the first provider-detail field."""
        self.query_one("#custom-provider-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Advance through fields, then dismiss with provider details."""
        input_id = event.input.id
        if input_id not in self._INPUT_ORDER:
            return
        event.stop()
        if input_id != self._INPUT_ORDER[-1]:
            self._focus_next(input_id)
            return
        result = self._collect_result()
        if result is not None:
            self.dismiss(result)

    def _focus_next(self, input_id: str) -> None:
        index = self._INPUT_ORDER.index(input_id)
        self.query_one(f"#{self._INPUT_ORDER[index + 1]}", Input).focus()

    def _collect_result(self) -> CustomProviderLoginResult | None:
        provider_name = self._field("custom-provider-name", "Provider name")
        if provider_name is None:
            return None
        base_url = self._field("custom-provider-base-url", "Base URL")
        if base_url is None:
            return None
        api_key_env = self._field("custom-provider-api-key-env", "API key environment variable")
        if api_key_env is None:
            return None
        models_text = self._field("custom-provider-models", "Model ids")
        if models_text is None:
            return None
        models = tuple(
            dict.fromkeys(item.strip() for item in models_text.split(",") if item.strip())
        )
        if not models:
            self.query_one("#custom-provider-help", Static).update(
                "At least one model id is required."
            )
            self.query_one("#custom-provider-models", Input).focus()
            return None
        default_model = self._field("custom-provider-default-model", "Default model")
        if default_model is None:
            return None
        if default_model not in models:
            self.query_one("#custom-provider-help", Static).update(
                "Default model must be included in the model list."
            )
            self.query_one("#custom-provider-default-model", Input).focus()
            return None
        api_key = self._field("custom-provider-api-key", "API key")
        if api_key is None:
            return None
        display_name = self.query_one("#custom-provider-display-name", Input).value.strip()
        return CustomProviderLoginResult(
            provider_name=provider_name,
            display_name=display_name or provider_name,
            base_url=base_url,
            api_key_env=api_key_env,
            models=models,
            default_model=default_model,
            api_key=api_key,
        )

    def _field(self, input_id: str, label: str) -> str | None:
        value = self.query_one(f"#{input_id}", Input).value.strip()
        if value:
            return value
        self.query_one("#custom-provider-help", Static).update(f"{label} is required.")
        self.query_one(f"#{input_id}", Input).focus()
        return None

    def action_back(self) -> None:
        """Return to the login method picker."""
        self.dismiss(_LoginFlowAction.BACK)

    def action_close(self) -> None:
        """Close the entire login flow."""
        self.dismiss(None)


class LoginScreen(ModalScreen[str | _LoginFlowAction | None]):
    """Password prompt for saving a provider API key."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+d", "close", "Close", priority=True),
    ]

    def __init__(self, provider: ProviderCatalogEntry, *, theme: TuiTheme) -> None:
        super().__init__()
        self.provider = provider
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the provider login prompt."""
        with Vertical(id="login-screen"):
            yield Static(f"Login: {self.provider.display_name}", id="login-title")
            yield Static("Paste this provider's API key.", id="login-help")
            yield Input(placeholder="Paste API key", password=True, id="login-api-key")
            yield Static("Enter saves - Escape goes back - Ctrl+D closes", id="login-footer")

    def on_mount(self) -> None:
        """Focus the API key field."""
        self.query_one("#login-api-key", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Dismiss with the submitted API key."""
        if event.input.id != "login-api-key":
            return
        event.stop()
        self.dismiss(event.value.strip() or None)

    def action_back(self) -> None:
        """Return to the login method picker without saving."""
        self.dismiss(_LoginFlowAction.BACK)

    def action_close(self) -> None:
        """Close the entire login flow."""
        self.dismiss(None)


class OAuthLoginScreen(ModalScreen[OAuthCredential | _LoginFlowAction | None]):
    """OAuth login flow for providers backed by subscription auth."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "back", "Back"),
        Binding("ctrl+d", "close", "Close", priority=True),
    ]

    def __init__(
        self,
        provider: ProviderCatalogEntry,
        *,
        theme: TuiTheme,
        login: Callable[[OAuthLoginCallbacks], Awaitable[OAuthCredential]] | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.theme = theme
        self._login = login
        self._manual_code_future: asyncio.Future[str] | None = None
        self._manual_code_value: str | None = None
        self._prompt_allows_empty = False

    def compose(self) -> ComposeResult:
        """Compose the OAuth login prompt."""
        with Vertical(id="login-screen"):
            yield Static(f"Login: {self.provider.display_name}", id="login-title")
            yield Static("Follow the provider instructions to complete login.", id="login-help")
            yield Static("", id="login-oauth-url")
            yield Input(
                placeholder="Paste redirect URL or authorization code",
                id="login-oauth-code",
            )
            yield Static("Enter submits - Escape goes back - Ctrl+D closes", id="login-footer")

    def on_mount(self) -> None:
        """Focus the manual-code field and start OAuth."""
        self.query_one("#login-oauth-code", Input).focus()
        self.run_worker(self._run_login(), exclusive=True)

    async def _run_login(self) -> None:
        try:
            oauth_provider = get_oauth_provider(self.provider.name)
            login = self._login or (oauth_provider.login if oauth_provider is not None else None)
            if login is None:
                raise RuntimeError(f"No OAuth implementation for {self.provider.name}")
            credential = await login(
                OAuthLoginCallbacks(
                    on_auth=self._show_auth,
                    on_device_code=self._show_device_code,
                    on_prompt=self._prompt_for_code,
                    on_select=self._select_option,
                    on_progress=self._show_progress,
                    on_manual_code_input=self._manual_code_input,
                )
            )
        except Exception as exc:  # noqa: BLE001 - surface OAuth failures in the TUI
            self.query_one("#login-help", Static).update(f"OAuth failed: {exc}")
            return
        self.dismiss(credential)

    def _show_auth(self, info: OAuthAuthInfo) -> None:
        self.query_one("#login-oauth-url", Static).update(info.url)
        if info.instructions:
            self.query_one("#login-help", Static).update(info.instructions)

    def _show_device_code(self, info: OAuthDeviceCodeInfo) -> None:
        self.query_one("#login-oauth-url", Static).update(info.verification_uri)
        self.query_one("#login-help", Static).update(
            f"Open the URL and enter code: {info.user_code}"
        )

    def _show_progress(self, message: str) -> None:
        self.query_one("#login-help", Static).update(message)

    async def _prompt_for_code(self, prompt: OAuthPrompt) -> str:
        self.query_one("#login-help", Static).update(prompt.message)
        self._prompt_allows_empty = prompt.allow_empty
        try:
            return await self._manual_code_input()
        finally:
            self._prompt_allows_empty = False

    async def _select_option(self, prompt: OAuthSelectPrompt) -> str | None:
        self.query_one("#login-help", Static).update(prompt.message)
        return prompt.options[0].id if prompt.options else None

    async def _manual_code_input(self) -> str:
        if self._manual_code_value is not None:
            return self._manual_code_value
        loop = asyncio.get_running_loop()
        self._manual_code_future = loop.create_future()
        try:
            return await self._manual_code_future
        finally:
            self._manual_code_future = None

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Resolve the manual OAuth code fallback."""
        if event.input.id != "login-oauth-code":
            return
        event.stop()
        value = event.value.strip()
        if not value and not self._prompt_allows_empty:
            return
        self._manual_code_value = value
        if self._manual_code_future is not None and not self._manual_code_future.done():
            self._manual_code_future.set_result(value)

    def action_back(self) -> None:
        """Return to the login method picker without saving credentials."""
        self._cancel_manual_code_input()
        self.dismiss(_LoginFlowAction.BACK)

    def action_close(self) -> None:
        """Close the entire login flow without saving credentials."""
        self._cancel_manual_code_input()
        self.dismiss(None)

    def _cancel_manual_code_input(self) -> None:
        if self._manual_code_future is not None and not self._manual_code_future.done():
            self._manual_code_future.cancel()


#: Keys an extension key interceptor is never consulted for. These flow
#: straight to normal dispatch so a buggy interceptor (one that returns True
#: too broadly) cannot swallow the session's hard interrupt/exit reflexes and
#: brick the TUI. Deliberately minimal — only the always-available escape
#: hatches: ``ctrl+d`` (the ``quit`` action, exits the app) and ``ctrl+c``
#: (Tau binds it to ``clear_prompt``, but it is the terminal-standard
#: SIGINT/interrupt reflex users hit to bail). NOT reserved: escape/enter/
#: arrows/tab/left/right — those are load-bearing for the tau-subagents
#: extension and must stay interceptable. This is Tau's counterpart to Pi's
#: ``RESERVED_KEYBINDINGS_FOR_EXTENSION_CONFLICTS`` (runner.ts:69), applied
#: here to the pre-dispatch interceptor rather than a registerShortcut API.
RESERVED_EXTENSION_INTERCEPTOR_KEYS: frozenset[str] = frozenset({"ctrl+c", "ctrl+d"})


class TauTuiApp(App[None]):
    """Interactive Textual frontend for a ``CodingSession``."""

    TITLE = "Tau"
    CSS = """
    Screen {
        layout: vertical;
        background: $tau-screen-background;
        color: $tau-screen-text;
    }

    Toast {
        background: $tau-chrome-background;
        color: $tau-chrome-text;
    }

    Toast .toast--title {
        color: $tau-accent;
    }

    #workspace {
        height: 1fr;
    }

    #sidebar {
        width: 40;
        min-width: 36;
        height: 1fr;
        padding: 1 1 1 2;
        background: $tau-prompt-background;
        border: none;
    }

    #sidebar-content {
        height: 1fr;
    }

    #sidebar-brand {
        height: auto;
        color: $tau-prompt-text;
    }

    TauTuiApp.-hide-sidebar #sidebar {
        display: none;
    }

    TauTuiApp.-hide-sidebar #main-pane {
        padding-left: 1;
    }

    TauTuiApp.-sidebar-right #sidebar {
        dock: right;
    }

    #main-pane {
        width: 1fr;
        padding: 1 1 0 1;
    }

    #transcript {
        height: 1fr;
        border: none;
        background: $tau-transcript-background;
        padding: 0 0 0 2;
        overflow-x: auto;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 1;
    }

    /* Component seam: generic extension mount points. */
    #main-slot {
        display: none;
        height: 1fr;
        border: none;
        background: $tau-transcript-background;
        padding: 0 0 0 2;
        overflow-x: auto;
        scrollbar-size-vertical: 0;
        scrollbar-size-horizontal: 1;
    }

    #above-prompt-slot {
        height: auto;
        max-height: 8;
        margin: 0 1 0 1;
        padding: 0;
        background: $tau-screen-background;
    }

    #below-prompt-slot {
        height: auto;
        max-height: 8;
        margin: 0 1 0 1;
        padding: 0;
        background: $tau-screen-background;
    }

    #queued-messages {
        height: auto;
        max-height: 8;
        margin: 0 1 1 1;
        padding: 0 1;
        background: $tau-screen-background;
        color: $tau-muted-text;
    }

    #prompt-row {
        height: auto;
        margin: 0 1 1 1;
    }

    #prompt-prefix {
        width: 2;
        height: 3;
        padding: 0 0 0 0;
        margin: 0;
        content-align: center middle;
        color: $tau-accent;
        text-style: bold;
    }

    #prompt {
        width: 1fr;
        height: auto;
        background: $tau-prompt-background;
        color: $tau-prompt-text;
        border: none;
        border-left: tall transparent;
        margin: 0;
        padding: 1 1;
        max-height: 8;
    }

    #prompt:focus {
        border-left: tall $tau-prompt-border;
    }

    #prompt.-shell-mode {
        border-left: tall $tau-accent;
    }

    #compact-session-info {
        height: auto;
        max-height: 3;
        margin: 0 1 1 1;
        padding: 0 1;
        color: $tau-muted-text;
    }

    #autocomplete {
        height: auto;
        max-height: 18;
        margin: 0 1 1 1;
        padding: 0 1;
        background: $tau-autocomplete-background;
        color: $tau-screen-text;
        border: tall $tau-border;
        overflow-y: auto;
    }

    SessionPickerScreen,
    TreePickerScreen,
    CommandOutputScreen {
        align: center middle;
    }

    #session-picker,
    #tree-picker {
        width: 76;
        max-width: 90%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: $tau-chrome-background;
        border: tall $tau-border;
    }

    #session-picker-title,
    #tree-picker-title {
        height: 1;
        color: $tau-chrome-text;
        text-style: bold;
        margin-bottom: 1;
    }

    #session-picker-search {
        height: 3;
        margin-bottom: 1;
        background: $tau-prompt-background;
        color: $tau-prompt-text;
        border: tall $tau-prompt-border;
    }

    #session-picker-list,
    #tree-picker-list {
        height: auto;
        max-height: 16;
        background: $tau-transcript-background;
        border: tall $tau-border;
    }

    ListView > ListItem.-highlight {
        background: $tau-highlight-background;
        color: $tau-highlight-text;
    }

    ListView > ListItem.-highlight Label {
        background: $tau-highlight-background;
        color: $tau-highlight-text;
    }

    #session-picker-help,
    #tree-picker-help {
        height: 1;
        margin-top: 1;
        color: $tau-muted-text;
    }

    ExtensionSelectScreen,
    ExtensionConfirmScreen,
    ExtensionInputScreen {
        align: center middle;
    }

    #extension-select,
    #extension-confirm,
    #extension-input {
        width: 76;
        max-width: 90%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: $tau-chrome-background;
        border: tall $tau-border;
    }

    #extension-select-title,
    #extension-confirm-title,
    #extension-input-title {
        height: auto;
        color: $tau-chrome-text;
        text-style: bold;
        margin-bottom: 1;
    }

    #extension-confirm-message {
        height: auto;
        color: $tau-chrome-text;
        margin-bottom: 1;
    }

    #extension-select-list,
    #extension-confirm-list {
        height: auto;
        max-height: 16;
        background: $tau-transcript-background;
        border: tall $tau-border;
    }

    #extension-input-field {
        background: $tau-transcript-background;
        border: tall $tau-border;
    }

    #extension-select-help,
    #extension-confirm-help,
    #extension-input-help {
        height: 1;
        margin-top: 1;
        color: $tau-muted-text;
    }

    #command-output {
        width: 76;
        max-width: 90%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: $tau-chrome-background;
        color: $tau-chrome-text;
        border: tall $tau-border;
    }

    #command-output-title {
        height: 1;
        color: $tau-chrome-text;
        text-style: bold;
        margin-bottom: 1;
    }

    #command-output-scroll {
        height: auto;
        max-height: 18;
        background: $tau-transcript-background;
        border: tall $tau-border;
    }

    #command-output-body {
        color: $tau-screen-text;
        padding: 1;
    }

    #command-output-help {
        height: 1;
        margin-top: 1;
        color: $tau-muted-text;
    }

    LoginMethodPickerScreen,
    LoginProviderPickerScreen,
    ThemePickerScreen,
    ModelPickerScreen {
        align: center middle;
    }

    #login-method-picker,
    #login-provider-picker,
    #theme-picker,
    #model-picker {
        width: 76;
        max-width: 90%;
        height: auto;
        max-height: 70%;
        padding: 1 2;
        background: $tau-chrome-background;
        color: $tau-chrome-text;
        border: tall $tau-border;
    }

    #login-method-title,
    #login-provider-title,
    #theme-picker-title,
    #model-picker-title {
        height: 1;
        color: $tau-chrome-text;
        text-style: bold;
        margin-bottom: 1;
    }

    #model-picker-tabs {
        height: 1;
        color: $tau-muted-text;
        margin-bottom: 1;
    }

    #login-method-list,
    #login-provider-list,
    #theme-picker-list,
    #model-picker-list {
        height: auto;
        max-height: 12;
        background: $tau-transcript-background;
        color: $tau-screen-text;
        border: tall $tau-border;
    }

    #login-method-list ListItem Label,
    #login-provider-list ListItem Label,
    #theme-picker-list ListItem Label,
    #model-picker-list ListItem Label {
        color: $tau-screen-text;
    }

    #login-method-list ListItem.-highlight Label,
    #login-provider-list ListItem.-highlight Label,
    #theme-picker-list ListItem.-highlight Label,
    #model-picker-list ListItem.-highlight Label {
        background: $tau-highlight-background;
        color: $tau-highlight-text;
    }

    #login-method-intro {
        height: 1;
        color: $tau-muted-text;
        margin-bottom: 1;
    }

    #login-method-list {
        height: auto;
        max-height: 10;
    }

    #login-provider-search,
    #model-picker-search {
        height: 3;
        margin-bottom: 1;
        background: $tau-prompt-background;
        color: $tau-prompt-text;
        border: tall $tau-prompt-border;
    }

    #login-method-help,
    #login-provider-help,
    #theme-picker-help,
    #model-picker-help {
        height: 1;
        margin-top: 1;
        color: $tau-muted-text;
    }

    CustomProviderLoginScreen,
    LoginScreen,
    OAuthLoginScreen {
        align: center middle;
    }

    #login-screen {
        width: 72;
        max-width: 92%;
        height: auto;
        padding: 1 2;
        background: $tau-chrome-background;
        border: tall $tau-border;
    }

    #login-title {
        height: 1;
        color: $tau-chrome-text;
        text-style: bold;
        margin-bottom: 1;
    }

    #login-help,
    #custom-provider-help {
        height: 1;
        color: $tau-muted-text;
        margin-bottom: 1;
    }

    #login-api-key,
    #login-oauth-code,
    #custom-provider-name,
    #custom-provider-display-name,
    #custom-provider-base-url,
    #custom-provider-api-key-env,
    #custom-provider-models,
    #custom-provider-default-model,
    #custom-provider-api-key {
        background: $tau-prompt-background;
        color: $tau-prompt-text;
        border: tall $tau-prompt-border;
        margin-bottom: 1;
    }

    #login-oauth-url {
        min-height: 1;
        max-height: 4;
        color: $tau-chrome-text;
        margin-bottom: 1;
    }

    #login-footer {
        height: 1;
        color: $tau-muted-text;
    }
    """
    BINDINGS: ClassVar[list[BindingEntry]] = []

    def __init__(
        self,
        session: CodingSession,
        *,
        tui_settings: TuiSettings | None = None,
        startup_message: str | None = None,
        startup_notice: str | None = None,
        startup_notices: Sequence[str] = (),
        initial_prompt: str | None = None,
    ) -> None:
        self.tui_settings = tui_settings or TuiSettings()
        self.startup_message = startup_message
        legacy_notices = (startup_notice,) if startup_notice else ()
        self.startup_notices = tuple((*startup_notices, *legacy_notices))
        self.initial_prompt = initial_prompt
        super().__init__()
        self._register_tau_textual_themes()
        # Assign the resolved theme's name: it is always registered, while the
        # raw settings value may name a custom theme that failed to load. The
        # guard keeps the watcher from persisting the fallback over the user's
        # configured theme.
        self._applying_settings_theme = True
        self.theme = self.tui_settings.resolved_theme.name
        self._applying_settings_theme = False
        self._bindings = BindingsMap(_app_bindings(self.tui_settings.keybindings))
        self.session = session
        self.state = TuiState(skills=session.skills)
        for notice in self.startup_notices:
            self.state.add_item("status", notice)
        if self.tui_settings.theme != self.tui_settings.resolved_theme.name:
            self.state.add_item(
                "status",
                f"Theme '{self.tui_settings.theme}' was not found; "
                f"using {self.tui_settings.resolved_theme.name}.",
            )
        self._prompt_history: tuple[str, ...] = ()
        self._load_session_messages_from_session()
        self.adapter = TuiEventAdapter(self.state)
        # Component seam: host-owned tracking of extension
        # widgets so a reload/rebind can force-clear them and a crash can
        # quarantine them. Must exist before _connect_extension_runtime, which
        # clears them on every bind.
        # `_extension_slot_widgets` holds the *intended* widget per key (the swap
        # target, set synchronously); `_extension_slot_mounted` tracks what is
        # actually mounted. A deferred remove() must fully drain before the next
        # mount of the same-id widget, so slot/main-view swaps run on a serialized
        # async continuation (see `_reconcile_slot`/`_reconcile_main_view`).
        self._extension_slot_widgets: dict[str, Widget] = {}
        self._extension_slot_mounted: dict[str, Widget] = {}
        self._extension_slot_slot_ids: dict[str, str] = {}
        self._extension_slot_locks: dict[str, asyncio.Lock] = {}
        self._extension_key_interceptors: list[KeyInterceptor] = []
        self._extension_main_view: _MainViewHandle | None = None
        self._extension_main_view_mounted: Widget | None = None
        self._extension_main_view_lock = asyncio.Lock()
        self._extension_swap_tasks: set[asyncio.Task[None]] = set()
        self._extension_component_failures_reported: set[str] = set()
        self._connect_extension_runtime(session)
        self._prompt_worker: Worker[None] | None = None
        self._compaction_worker: Worker[None] | None = None
        self._prompt_run_id = 0
        self._optimistic_user_messages: list[tuple[int, str]] = []
        self._completion_state = CompletionState()
        self._completion_visible_line_budget: int | None = None
        self._activity_frame = 0
        self._activity_timer: Timer | None = None
        self._last_tool_timer_refresh_at = 0.0
        self._last_activity_indicator_key: tuple[object, ...] | None = None
        self._last_queue_render_key: tuple[object, ...] | None = None
        self._terminal_title = TerminalTitleController()
        self._active_notification_keys: set[tuple[str, str]] = set()
        self._supports_pyperclip: bool | None = None
        self._sync_session_title()

    def _sync_session_title(self) -> None:
        """Reflect the active session name in the terminal tab title."""
        self._sync_terminal_title()

    def _sync_terminal_title(self) -> None:
        """Reflect the active session name and running state in the terminal tab title."""
        self._terminal_title.update(
            getattr(self.session, "session_title", None),
            running=self.state.running,
            frame=self._activity_frame,
        )

    def _sync_text_selection_state(self) -> None:
        """Disable native text selection while the transcript is mutating."""
        type(self).ALLOW_SELECT = not self.state.running
        if self.state.running and self.screen_stack:
            with suppress(Exception):
                self.screen.clear_selection()

    def copy_to_clipboard(self, text: str) -> None:
        """Copy text using pyperclip when available, then Textual's fallback."""
        if self._supports_pyperclip is None:
            try:
                import pyperclip  # type: ignore[import-untyped]
            except ImportError:
                self._supports_pyperclip = False
            else:
                self._supports_pyperclip = True
        if self._supports_pyperclip:
            import pyperclip

            with suppress(Exception):
                pyperclip.copy(text)
        super().copy_to_clipboard(text)

    def _register_tau_textual_themes(self) -> None:
        """Register Tau themes with Textual's theme system.

        Textual exposes its own theme menu and command palette entries. Registering
        Tau's built-in themes there makes those controls update the same theme as
        `/theme` instead of changing only Textual's chrome.
        """
        self._registered_themes.clear()
        for theme_name in available_tui_theme_names():
            self.register_theme(_textual_theme_for_tau_theme(theme_name))

    def _watch_theme(self, theme_name: str) -> None:
        """Keep Textual theme changes synchronized with Tau's durable TUI theme."""
        super()._watch_theme(theme_name)
        if theme_name not in available_tui_theme_names():
            return
        if getattr(self, "_applying_settings_theme", False):
            return
        tau_theme: TuiThemeName = theme_name
        if self.tui_settings.theme == tau_theme:
            return
        self._replace_tui_settings(theme=tau_theme)
        save_tui_settings(self.tui_settings)

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Return Tau-specific CSS variables for the selected TUI theme."""
        variables = super().get_theme_variable_defaults()
        return {**variables, **_theme_css_variables(self.tui_settings.resolved_theme)}

    def compose(self) -> ComposeResult:
        """Compose the TUI widgets."""
        with Horizontal(id="workspace"):
            yield SessionSidebar(id="sidebar")
            with Vertical(id="main-pane"):
                yield TranscriptView(
                    id="transcript",
                    min_width=1,
                    wrap=True,
                    highlight=True,
                    markup=False,
                )
                # Component seam: host-managed mount points for
                # extension widgets. Empty until an extension mounts into them.
                yield Container(id="main-slot")
                yield Container(id="above-prompt-slot")
                yield Static("", id="queued-messages")
                with Horizontal(id="prompt-row"):
                    yield Static("τ", id="prompt-prefix")
                    yield PromptInput(
                        placeholder=PROMPT_PLACEHOLDER,
                        id="prompt",
                        tui_keybindings=self.tui_settings.keybindings,
                    )
                yield CompactSessionInfo(id="compact-session-info")
                yield Static("", id="autocomplete")
                yield Container(id="below-prompt-slot")

    async def on_mount(self) -> None:
        """Focus the prompt when the app starts."""
        prompt = self.query_one(PromptInput)
        prompt.shell_mode_style = self.tui_settings.resolved_theme.accent
        self._sync_prompt_shell_mode(prompt.text)
        prompt.focus()
        self._update_responsive_layout(self.size.width, self.size.height)
        self._apply_sidebar_position()
        self._refresh()
        self._sync_text_selection_state()
        self._refresh_completions()
        if self.startup_message:
            self._notify(self.startup_message, severity="warning")
        # UI is live and the bridge is installed (__init__) — release the
        # deferred session_start so handlers can notify / open dialogs.
        await self.session.emit_pending_session_start()
        if self.initial_prompt and self.initial_prompt.strip():
            await self._submit_prompt(self.initial_prompt.strip())

    async def on_event(self, event: events.Event) -> None:
        """Consult extension key interceptors before Textual's dispatch.

        Ports Pi's ``onTerminalInput``: a registered interceptor sees a key at
        the earliest point in key processing — before tau's app-level priority
        bindings (``down``/``up``/``tab``/``alt+enter`` in ``_app_bindings``)
        and before the focused widget. Textual's ``App.on_event`` runs
        ``_check_bindings(key, priority=True)`` ahead of forwarding a key to the
        focused widget, so a focused extension widget would otherwise never
        receive those keys; this pre-dispatch hook is the only place an
        extension can own them.

        Interceptors are consulted only on the main screen (never while a modal
        dialog/picker sits on the screen stack) and only when at least one is
        registered, so the default path is untouched. Interceptors therefore
        see EVERY main-screen key regardless of focus and must self-gate.

        The hard interrupt/exit keys in
        :data:`RESERVED_EXTENSION_INTERCEPTOR_KEYS` are skipped entirely, so
        they always reach normal dispatch even behind a misbehaving interceptor.
        """
        if (
            isinstance(event, events.Key)
            and not event.is_forwarded
            and event.key not in RESERVED_EXTENSION_INTERCEPTOR_KEYS
            and self._extension_key_interceptors
            and len(self.screen_stack) <= 1
            and self._run_extension_key_interceptors(event, self._current_prompt_text())
        ):
            event.stop()
            event.prevent_default()
            return
        await super().on_event(event)

    def on_unmount(self) -> None:
        """Stop activity animations and drop extension widgets on teardown."""
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None
        self._terminal_title.restore()
        self._clear_extension_components()

    def on_resize(self, event: Resize) -> None:
        """Update responsive chrome when the terminal changes size."""
        self._completion_visible_line_budget = None
        self._update_responsive_layout(event.size.width, event.size.height)

    def on_click(self, event: events.Click) -> None:
        """Return keyboard focus to the prompt after clicks in the main TUI."""
        if event.button != 1:
            return
        if self._extension_main_view is not None:
            # An extension main view (e.g. a subagent conversation viewer) owns
            # the main area and its keyboard; yanking focus back to the prompt
            # would silently reroute every key — esc, toggles, typed text — to
            # the main chat. Clicking the prompt itself still focuses it via
            # Textual's native mouse-down focus.
            return
        with suppress(NoMatches):
            self.screen.query_one("#prompt", PromptInput).focus()

    @on(events.TextSelected)
    async def on_text_selected(self) -> None:
        """Optionally copy selected text automatically."""
        active_screen = self.screen
        if not (
            self.tui_settings.auto_copy_selection
            or getattr(active_screen, "auto_copy_selection", False)
        ):
            return
        selection = active_screen.get_selected_text()
        if selection:
            self.copy_to_clipboard(selection)
            self._notify("Copied selection to clipboard.")

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Update prompt autocomplete when the prompt text changes."""
        if event.text_area.id != "prompt":
            return
        prompt = self.query_one("#prompt", PromptInput)
        prompt.sync_pending_paste()
        self._sync_prompt_shell_mode(event.text_area.text)
        self._completion_state = self._build_completion_state(event.text_area.text)
        self._refresh_completions()

    async def action_submit_prompt(self) -> None:
        """Submit the current prompt text or slash command."""
        await self._submit_prompt_from_editor(streaming_behavior="steer")

    async def action_submit_follow_up(self) -> None:
        """Submit the current prompt as a queued follow-up while running."""
        await self._submit_prompt_from_editor(streaming_behavior="follow_up")

    async def _submit_prompt_from_editor(
        self,
        *,
        streaming_behavior: Literal["steer", "follow_up"],
    ) -> None:
        prompt = self.query_one("#prompt", PromptInput)
        raw_text = prompt.text_for_submission()
        applied_completion = self._apply_selected_completion(raw_text)
        if applied_completion is not None and applied_completion != raw_text:
            prompt.text = applied_completion
            prompt._clear_pending_paste()
            prompt.move_cursor(_text_end_location(applied_completion))
            self._completion_state = self._build_completion_state(applied_completion)
            self._refresh_completions()
            return

        text = raw_text.strip()
        if not text:
            prompt.text = ""
            prompt._clear_pending_paste()
            self._completion_state = CompletionState()
            self._refresh_completions()
            return

        if self._is_compaction_active():
            if text.startswith("/compact"):
                self._notify("A compaction is already running.", severity="warning")
            else:
                prompt.text = raw_text
                prompt.move_cursor(_text_end_location(raw_text))
                self._notify(
                    "Compaction is still running. You can keep editing, but wait to submit.",
                    severity="warning",
                )
            return

        prompt.text = ""
        prompt._clear_pending_paste()
        self._completion_state = CompletionState()
        self._refresh_completions()

        terminal_command = parse_terminal_command(text)
        if terminal_command is not None:
            self.run_worker(
                self._run_terminal_command(
                    terminal_command.command,
                    add_to_context=terminal_command.add_to_context,
                ),
                exclusive=True,
            )
            return

        command = self.session.handle_command(text)
        if command.handled:
            if command.clear_requested:
                self.state.clear()
            if command.reload_requested:
                try:
                    summary = await self.session.reload()
                except ValueError as exc:
                    command = replace(command, message=f"Could not reload: {exc}")
                else:
                    command = replace(command, message=format_reload_summary(summary))
            if command.new_session_requested:
                await self._new_session()
            if command.compact_summary is not None:
                if self._is_compaction_active():
                    self._notify("A compaction is already running.", severity="warning")
                elif self._is_agent_or_queue_active():
                    prompt.text = raw_text
                    prompt.move_cursor(_text_end_location(raw_text))
                    self._notify(
                        "Wait for the current agent turn and queued messages to finish "
                        "before compacting.",
                        severity="warning",
                    )
                    return
                else:
                    self._compaction_worker = self.run_worker(
                        self._run_compaction(command.compact_summary),
                        exclusive=False,
                    )
            if command.export_requested:
                try:
                    exported_path = await self.session.export(
                        command.export_destination,
                        format=command.export_format,
                    )
                    self._notify(f"Exported session to {exported_path}")
                except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
                    self._notify(f"Could not export session: {exc}", severity="error")
            if command.resume_session_id is not None:
                await self._resume_session(command.resume_session_id)
            if command.resume_picker_requested:
                self.action_open_session_picker()
            if command.tree_picker_requested:
                if self._is_agent_or_queue_active():
                    prompt.text = raw_text
                    prompt.move_cursor(_text_end_location(raw_text))
                    self._notify(TREE_RUNNING_MESSAGE, severity="warning")
                    return
                await self._open_tree_picker()
            if command.login_picker_requested:
                self._open_login_picker()
            if command.custom_provider_login_requested:
                self._open_custom_provider_login()
            if command.login_provider is not None:
                self._open_login(command.login_provider, method=command.login_method)
            if command.logout_picker_requested:
                self._open_logout_picker()
            if command.logout_provider is not None:
                self._logout(command.logout_provider)
            if command.model_picker_requested:
                self._open_model_picker()
            if command.scoped_models_picker_requested:
                self._open_scoped_models_picker()
            if command.theme_picker_requested:
                self._open_theme_picker()
            if command.thinking_level is not None:
                await self._set_thinking_level(command.thinking_level)
            if command.theme is not None:
                self._set_tui_theme(command.theme)
            self.state.set_skills(self.session.skills)
            if command.message:
                if _command_message_uses_notification(text, command.message):
                    self._notify(command.message)
                elif _command_message_uses_transcript(text):
                    self._append_command_message(text, command.message)
                else:
                    self._show_command_message(text, command.message)
            self._refresh()
            if command.exit_requested:
                self.exit()
            return

        if self.state.running:
            self._remember_prompt(text)
            await self._queue_prompt(text, streaming_behavior=streaming_behavior)
            return

        self._remember_prompt(text)
        await self._submit_prompt(text)

    def _remember_prompt(self, text: str) -> None:
        """Remember a submitted user prompt for lightweight input recall."""
        if not text.strip():
            return
        self._prompt_history = (*self._prompt_history, text)

    def _load_session_messages_from_session(self) -> None:
        """Load visible session messages and reseed prompt history from them."""
        self.state.load_messages(self.session.messages)
        self._prompt_history = tuple(
            message.text
            for message in self.session.messages
            if isinstance(message, UserMessage) and message.text.strip()
        )

    def _is_compaction_active(self) -> bool:
        """Return whether a manual compaction worker is still running."""
        worker = self._compaction_worker
        return worker is not None and not worker.is_finished and not worker.is_cancelled

    def _is_agent_or_queue_active(self) -> bool:
        """Return whether compaction would race an active or queued agent turn."""
        self._sync_queue_state()
        worker = self._prompt_worker
        is_worker_active = worker is not None and not worker.is_finished and not worker.is_cancelled
        is_session_running = bool(getattr(self.session, "is_running", False))
        return (
            self.state.running
            or is_session_running
            or is_worker_active
            or self.state.queued_message_count > 0
        )

    async def _run_compaction(self, summary: str) -> None:
        """Run manual compaction without disabling prompt editing."""
        self.state.clear()
        self.state.add_item("status", "Compacting session…")
        self._refresh()
        try:
            compact_message = await self.session.compact(summary)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
            return
        finally:
            self._compaction_worker = None
        self.state.clear()
        self.state.set_skills(self.session.skills)
        self._load_session_messages_from_session()
        self._notify(compact_message)
        self._refresh()

    async def _submit_prompt(
        self,
        text: str,
        *,
        source: Literal["interactive", "extension"] = "interactive",
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        """Add a prompt to the transcript and start the agent worker."""
        self._prompt_run_id += 1
        run_id = self._prompt_run_id
        # Custom messages are never rendered optimistically: the optimistic
        # dedupe matches on exact content equality with the post-expansion
        # event (see _consume_optimistic_user_event), and a mismatch would
        # double-render. They render once, from the confirmed user event,
        # which carries their custom_type/details.
        if custom_type is None and _should_optimistically_render_prompt(text):
            self._optimistic_user_messages.append((run_id, text))
            await self._append_optimistic_user_message(text)
        self._prompt_worker = self.run_worker(
            self._run_prompt(text, run_id, source=source, custom_type=custom_type, details=details),
            exclusive=True,
        )

    async def _append_optimistic_user_message(
        self,
        text: str,
        *,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        """Render a submitted user message immediately without rebuilding the transcript."""
        start_index = len(self.state.items)
        self.state.add_user_message(text, custom_type=custom_type, details=details)
        self._follow_transcript_output()
        if not self.screen_stack:
            self._refresh()
            return
        theme = self.tui_settings.resolved_theme
        try:
            transcript = self.query_one("#transcript", TranscriptView)
        except NoMatches:
            self._refresh()
            return
        for item in self.state.items[start_index:]:
            await transcript.append_item(
                item,
                theme=theme,
                show_tool_results=self.state.show_tool_results,
                scroll_end=True,
                custom_markup=self.state.resolve_custom_markup(
                    item, expanded=self.state.show_tool_results
                ),
            )
        self._refresh_chrome(theme=theme)

    def _consume_optimistic_user_event(self, event: CodingSessionEvent, *, run_id: int) -> bool:
        """Return whether a user event confirms an already-rendered optimistic message."""
        if not isinstance(event, MessageEndEvent) or not isinstance(event.message, UserMessage):
            return False
        for index, (pending_run_id, pending_text) in enumerate(self._optimistic_user_messages):
            if pending_run_id == run_id and pending_text == event.message.content:
                del self._optimistic_user_messages[index]
                return True
        return False

    def _replace_transformed_optimistic_user_message(
        self, event: CodingSessionEvent, *, run_id: int
    ) -> bool:
        """Reconcile a transformed prompt with its optimistic render.

        An extension `input` hook may transform the submitted text inside
        session.prompt, so the confirmed UserMessage no longer matches the
        optimistically rendered original (the exact-equality path above).
        Rewrite the optimistic item in place and redraw, instead of letting
        the confirmed event append a second user item alongside the stale
        original. Runs after _consume_optimistic_user_event, so it only fires
        when this run's pending optimistic text mismatches — the run's own
        prompt confirmation is the first user event of the run, so a queued
        steering/follow-up user message can never be mistaken for it.
        """
        if not isinstance(event, MessageEndEvent) or not isinstance(event.message, UserMessage):
            return False
        for index, (pending_run_id, pending_text) in enumerate(self._optimistic_user_messages):
            if pending_run_id != run_id:
                continue
            del self._optimistic_user_messages[index]
            for item in reversed(self.state.items):
                if item.role == "user" and item.text == pending_text:
                    item.text = event.message.text
                    break
            self._refresh()
            self._sync_session_title()
            return True
        return False

    def _clear_optimistic_user_messages(self, *, run_id: int) -> None:
        """Drop unconfirmed optimistic messages once their run is no longer active."""
        self._optimistic_user_messages = [
            pending for pending in self._optimistic_user_messages if pending[0] != run_id
        ]

    async def _append_confirmed_user_message(self, message: AgentMessage) -> None:
        """Render a non-optimistic user/custom event incrementally when possible."""
        if isinstance(message, UserMessage):
            await self._append_optimistic_user_message(message.text)
            return
        if isinstance(message, CustomMessage):
            await self._append_optimistic_user_message(
                message.text,
                custom_type=message.custom_type,
                details=message.details if isinstance(message.details, dict) else None,
            )
            return
        self._refresh()

    def _connect_extension_runtime(self, session: CodingSession) -> None:
        """Give the extension runtime a UI bridge and an idle-run entry point."""
        runtime = getattr(session, "extension_runtime", None)
        if runtime is None:
            return
        # Force-clear any extension widgets before installing the new bridge.
        # This runs once, at construction; later teardowns (/reload, resume,
        # new) come through the installed bridge's clear_components(), driven
        # by the runtime, so extension widgets and key interceptors never
        # survive a world they were mounted in.
        self._clear_extension_components()
        runtime.set_ui_bridge(_TuiExtensionUiBridge(self))
        runtime.set_turn_requested_callback(self._on_extension_turn_requested)
        # Let the transcript render custom messages via registered renderers.
        self.state.custom_renderer = runtime.render_custom_message
        # Let tool calls render through their tool's render_call, if any.
        self.state.tool_call_renderer = runtime.render_tool_call
        # And tool results through their tool's render_result, if any.
        self.state.tool_result_renderer = runtime.render_tool_result

    def _on_extension_turn_requested(
        self,
        content: str,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        """Deliver an extension message through the serialized prompt path."""
        self.call_later(self._deliver_extension_message, content, custom_type, details)

    async def _deliver_extension_message(
        self,
        content: str,
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        if self.session.is_running or self._prompt_worker is not None:
            # A run started while the delivery was in flight; drain with it.
            queue_follow_up = getattr(self.session, "queue_follow_up_message", None)
            if callable(queue_follow_up):
                queue_follow_up(content, custom_type=custom_type, details=details)
            return
        await self._submit_prompt(
            content, source="extension", custom_type=custom_type, details=details
        )

    # -- component seam ------------------------------------------------------

    def _current_prompt_text(self) -> str:
        """Return the prompt-editor text, or "" before the prompt exists."""
        try:
            return self.query_one("#prompt", PromptInput).text
        except NoMatches:
            return ""

    def _register_extension_key_interceptor(self, handler: KeyInterceptor) -> Callable[[], None]:
        """Register a pre-dispatch key interceptor; return an unsubscribe fn."""
        self._extension_key_interceptors.append(handler)

        def unsubscribe() -> None:
            with suppress(ValueError):
                self._extension_key_interceptors.remove(handler)

        return unsubscribe

    def _run_extension_key_interceptors(self, event: Key, text: str) -> bool:
        """Consult interceptors; return True if one consumed the key.

        Each call is guarded: a raising interceptor is diagnosed once and
        treated as "not consumed", so a broken interceptor degrades to normal
        typing rather than a dead prompt.
        """
        for interceptor in tuple(self._extension_key_interceptors):
            try:
                if interceptor(event, text):
                    return True
            except Exception as exc:  # noqa: BLE001 - isolation boundary
                # Notify like the other failure classes so a broken interceptor
                # is not silently invisible, and dedup per-interceptor so a
                # second faulty handler still gets diagnosed.
                self._record_extension_component_failure(
                    f"key_interceptor:{id(interceptor)}", exc, notify=True
                )
        return False

    def _schedule_extension_swap(self, coro: Coroutine[object, object, None]) -> None:
        """Run a slot/main-view reconcile coroutine on the app loop.

        The task is retained until it finishes so it cannot be garbage-collected
        mid-flight (asyncio only holds a weak reference). If there is no running
        loop (only possible outside a live TUI), the coroutine is closed rather
        than left un-awaited.
        """
        try:
            task = asyncio.ensure_future(coro)
        except RuntimeError:  # no running loop — not a live TUI
            coro.close()
            return
        self._extension_swap_tasks.add(task)
        task.add_done_callback(self._extension_swap_tasks.discard)

    @staticmethod
    def _string_slot_widget(lines: Sequence[str]) -> Static:
        """Build a slot ``Static`` from display lines (Rich markup, safe fallback).

        Joins ``lines`` with newlines and parses them as Rich markup; if the
        markup is malformed the literal text is shown instead, mirroring the
        custom-message renderer's guard so a bad string never crashes the TUI.
        """
        content = "\n".join(lines)
        return Static(_custom_markup_to_text(content))

    def _set_extension_slot_widget(
        self,
        key: str,
        content: SlotWidgetContent | None,
        placement: Placement,
    ) -> None:
        """Mount an extension widget into a prompt-adjacent slot, or unmount it.

        ``content`` is a ``factory(theme)`` callable, a list of display lines
        the host renders into a ``Static``, or ``None`` to unmount. The string
        form is normalized into a factory here so the reconcile/quarantine/
        replace machinery below is untouched.

        The intended widget is recorded synchronously so mid-swap reads (clear,
        quarantine, refresh) see what *should* occupy the slot; the actual
        mount/unmount runs on a serialized continuation so a deferred remove()
        of a same-id widget fully drains before the replacement mounts (else the
        DOM briefly holds two widgets with one id -> ``DuplicateIds``).
        """
        factory: SlotWidgetFactory | None
        if content is None:
            factory = None
        elif callable(content):
            # Check callable() first: a Sequence[str] test must never swallow a
            # factory (and a factory is not a Sequence).
            factory = content
        else:
            # A plain list of display lines: build the widget host-side so the
            # extension needs no Textual import. A bare str is treated as one
            # line (never split into characters).
            lines = [content] if isinstance(content, str) else list(content)
            factory = lambda _theme: self._string_slot_widget(lines)  # noqa: E731
        new_widget: Widget | None = None
        if factory is not None:
            try:
                new_widget = factory(self.tui_settings.resolved_theme)
            except Exception as exc:  # noqa: BLE001 - isolation boundary
                self._record_extension_component_failure(f"slot:{key}", exc, notify=True)
                return
        slot_id = "above-prompt-slot" if placement == "above_prompt" else "below-prompt-slot"
        if new_widget is None:
            self._extension_slot_widgets.pop(key, None)
            self._extension_slot_slot_ids.pop(key, None)
        else:
            self._extension_slot_widgets[key] = new_widget
            self._extension_slot_slot_ids[key] = slot_id
        self._schedule_extension_swap(self._reconcile_slot(key))

    async def _reconcile_slot(self, key: str) -> None:
        """Make the mounted slot widget match the intended target (serialized).

        Reads the *live* target each time (not a snapshot), so a burst of set
        calls collapses to "last writer wins": the first continuation removes the
        stale mount, later ones find the target already satisfied and no-op.
        """
        lock = self._extension_slot_locks.setdefault(key, asyncio.Lock())
        async with lock:
            target = self._extension_slot_widgets.get(key)
            mounted = self._extension_slot_mounted.get(key)
            if mounted is not None and mounted is not target:
                with suppress(Exception):
                    await mounted.remove()
                if self._extension_slot_mounted.get(key) is mounted:
                    self._extension_slot_mounted.pop(key, None)
            # Re-read after the await; the target may have changed meanwhile.
            target = self._extension_slot_widgets.get(key)
            if target is None or self._extension_slot_mounted.get(key) is target:
                return
            slot_id = self._extension_slot_slot_ids.get(key, "below-prompt-slot")
            try:
                self.query_one(f"#{slot_id}", Container).mount(target)
            except Exception as exc:  # noqa: BLE001 - isolation boundary
                if self._extension_slot_widgets.get(key) is target:
                    self._extension_slot_widgets.pop(key, None)
                    self._extension_slot_slot_ids.pop(key, None)
                self._record_extension_component_failure(f"slot:{key}", exc, notify=True)
                return
            self._extension_slot_mounted[key] = target

    def _open_extension_main_view(self, factory: MainViewFactory) -> MainViewHandle:
        """Open a display-toggled main-area view mounting ``factory(handle, theme)``.

        Prompt focus is intentionally left where it is (the extension widget can
        focus its own composer), so a registered key interceptor keeps firing
        while the prompt is focused and can close the view on Esc.

        The handle is returned synchronously (the factory needs it and callers
        store it at once), but the mount is sequenced after any previous view's
        remove() drains, so switching views never collides on the shared main
        slot. ``is_open`` reports the *intended* state: the new handle is open
        the instant it is returned even though its widget mounts a tick later.
        """
        handle = _MainViewHandle(self, asyncio.get_event_loop().create_future())
        try:
            widget = factory(handle, self.tui_settings.resolved_theme)
        except Exception as exc:  # noqa: BLE001 - isolation boundary
            self._record_extension_component_failure("main_view", exc, notify=True)
            # Nothing awaits this handle (the extension gets the dead one), but
            # resolve it anyway so no future leaks unresolved.
            handle._resolve(None)
            return _DeadMainViewHandle()
        handle.widget = widget
        previous = self._extension_main_view
        if previous is not None and previous is not handle:
            # Superseded (last writer wins): its close() becomes a no-op so a
            # stale Esc/unmount can't tear down the view that replaced it, and
            # its pending wait() resolves with None.
            self._release_main_view_handle(previous)
        self._extension_main_view = handle
        self._schedule_extension_swap(self._reconcile_main_view())
        return handle

    async def _reconcile_main_view(self) -> None:
        """Make the mounted main view match the intended handle (serialized)."""
        async with self._extension_main_view_lock:
            target = self._extension_main_view
            target_widget = target.widget if target is not None else None
            mounted = self._extension_main_view_mounted
            if mounted is not None and mounted is not target_widget:
                with suppress(Exception):
                    await mounted.remove()
                if self._extension_main_view_mounted is mounted:
                    self._extension_main_view_mounted = None
            # Re-read after the await.
            target = self._extension_main_view
            target_widget = target.widget if target is not None else None
            if target_widget is not None and self._extension_main_view_mounted is not target_widget:
                try:
                    slot = self.query_one("#main-slot", Container)
                    slot.mount(target_widget)
                except Exception as exc:  # noqa: BLE001 - isolation boundary
                    if self._extension_main_view is target:
                        self._extension_main_view = None
                    self._release_main_view_handle(target)
                    self._record_extension_component_failure("main_view", exc, notify=True)
                    self._restore_main_transcript()
                    return
                self._extension_main_view_mounted = target_widget
                with suppress(NoMatches):
                    self.query_one("#transcript", TranscriptView).display = False
                slot.display = True
            elif self._extension_main_view is None and self._extension_main_view_mounted is None:
                # A close (target cleared) with nothing left to show.
                self._restore_main_transcript()

    def _close_extension_main_view(self, handle: _MainViewHandle) -> None:
        """Unmount a main view and restore the main transcript (sequenced)."""
        if self._extension_main_view is not handle:
            return
        self._extension_main_view = None
        self._schedule_extension_swap(self._reconcile_main_view())

    def _release_main_view_handle(self, handle: _MainViewHandle | None) -> None:
        """Mark a host-torn-down handle closed and resolve its ``wait()`` with None.

        Used by the teardown paths the host drives itself (supersede, session
        rebind, mount failure, quarantine) — as opposed to an explicit
        ``handle.close(result)`` from the extension — so a pending ``wait()``
        never leaks unresolved and ``is_open`` reports False.
        """
        if handle is None:
            return
        handle._open = False
        handle._resolve(None)

    def _restore_main_transcript(self) -> None:
        """Hide the main slot and bring the main transcript back into focus."""
        if not self.screen_stack:
            return
        with suppress(NoMatches):
            self.query_one("#main-slot", Container).display = False
        with suppress(NoMatches):
            pane = self.query_one("#transcript", TranscriptView)
            pane.display = True
            # Re-anchor the restored main transcript so returning to a live
            # conversation lands at the bottom (mirrors _follow_transcript_output).
            pane.follow_output()
        with suppress(NoMatches):
            self.query_one("#prompt", PromptInput).focus()

    def _refresh_extension_components(self) -> None:
        """Re-render all mounted extension widgets (analog of requestRender)."""
        for widget in tuple(self._extension_slot_widgets.values()):
            with suppress(Exception):
                widget.refresh()
        handle = self._extension_main_view
        if handle is not None and handle.widget is not None:
            with suppress(Exception):
                handle.widget.refresh()

    def _clear_extension_components(self) -> None:
        """Force-clear every tracked extension widget, view, and interceptor.

        The runtime drives this through the UI bridge on `/reload` and session
        rebinds (resume/new); it also runs on app teardown, so a leaked
        extension widget never survives a session switch. Intent is cleared
        synchronously — mid-swap reads and in-flight continuations then see
        empty state — while the actual unmounts run on the same serialized
        per-key reconciles as ordinary swaps, so a clear followed immediately
        by a re-mount of a same-id widget (a session_start handler re-mounting
        after a rebind) can never hold two widgets with one id
        (``DuplicateIds``).
        """
        slot_keys = {*self._extension_slot_widgets, *self._extension_slot_mounted}
        self._extension_slot_widgets.clear()
        self._extension_slot_slot_ids.clear()
        for key in slot_keys:
            self._schedule_extension_swap(self._reconcile_slot(key))
        handle = self._extension_main_view
        self._extension_main_view = None
        self._release_main_view_handle(handle)
        self._schedule_extension_swap(self._reconcile_main_view())
        self._extension_key_interceptors.clear()
        # A recurring failure context must notify again in the new world.
        self._extension_component_failures_reported.clear()

    def _tracked_extension_widgets(self) -> tuple[Widget, ...]:
        """Return every extension widget the host currently tracks (intended or mounted)."""
        widgets: list[Widget] = []
        seen: set[int] = set()
        for widget in (
            *self._extension_slot_widgets.values(),
            *self._extension_slot_mounted.values(),
        ):
            if id(widget) not in seen:
                seen.add(id(widget))
                widgets.append(widget)
        handle = self._extension_main_view
        main_widgets = (
            handle.widget if handle is not None else None,
            self._extension_main_view_mounted,
        )
        for main_widget in main_widgets:
            if main_widget is not None and id(main_widget) not in seen:
                seen.add(id(main_widget))
                widgets.append(main_widget)
        return tuple(widgets)

    def _extension_root_for(self, widget: Widget, tracked: tuple[Widget, ...]) -> Widget | None:
        """Return the tracked extension root that owns ``widget``, if any."""
        node: Widget | None = widget
        while node is not None:
            for root in tracked:
                if node is root:
                    return root
            node = node.parent if isinstance(node.parent, Widget) else None
        return None

    def _quarantine_extension_widget(self, error: BaseException) -> bool:
        """Remove the tracked extension widget implicated in ``error``.

        Returns True when a culprit was found and torn down (so the app can
        swallow the exception and stay alive), False otherwise (so core bugs
        still surface). Textual runs ``render`` on the compositor's own reflow
        loop, so a child's render/compose/on_mount crash cannot be caught at the
        mount site; walking the traceback for a frame owned by a tracked widget
        is the only handle we get.
        """
        tracked = self._tracked_extension_widgets()
        if not tracked:
            return False
        tb = error.__traceback__
        culprit: Widget | None = None
        while tb is not None:
            candidate = tb.tb_frame.f_locals.get("self")
            if isinstance(candidate, Widget):
                root = self._extension_root_for(candidate, tracked)
                if root is not None:
                    culprit = root
                    break
            tb = tb.tb_next
        if culprit is None:
            return False
        # Suppress the ghost first: a widget that crashed in on_mount never
        # finished mounting, so remove() cannot fully prune it, but hiding and
        # disabling it makes it inert and invisible. A render-crash widget
        # removes cleanly. Either way the app keeps running.
        with suppress(Exception):
            culprit.display = False
        with suppress(Exception):
            culprit.disabled = True
        if (
            self._extension_main_view is not None and self._extension_main_view.widget is culprit
        ) or self._extension_main_view_mounted is culprit:
            if self._extension_main_view_mounted is culprit:
                self._extension_main_view_mounted = None
            handle = self._extension_main_view
            self._extension_main_view = None
            self._release_main_view_handle(handle)
            with suppress(Exception):
                culprit.remove()
            self._restore_main_transcript()
        else:
            for tracker in (self._extension_slot_widgets, self._extension_slot_mounted):
                key = next((k for k, w in tracker.items() if w is culprit), None)
                if key is not None:
                    tracker.pop(key, None)
            with suppress(Exception):
                culprit.remove()
        self._record_extension_component_failure(f"render:{id(culprit)}", error, notify=True)
        return True

    def _handle_exception(self, error: Exception) -> None:
        """Quarantine a crashing extension widget instead of tearing down.

        Overrides Textual's private ``App._handle_exception`` (there is no
        public error hook — ``hasattr(App, "on_exception")`` is False on the
        pinned Textual). If the traceback touches a tracked extension widget we
        remove it and keep running; otherwise we defer to Textual's default so
        core's own bugs still surface. This private-API coupling is a contract
        cost the component-seam experiment deliberately accepts.
        """
        if self._quarantine_extension_widget(error):
            return
        super()._handle_exception(error)

    def _record_extension_component_failure(
        self, context: str, error: BaseException, *, notify: bool = False
    ) -> None:
        """Diagnose an extension-component failure once per context.

        The notification carries a short exception summary so the failure is
        identifiable at a glance; the full traceback goes to the app log for a
        post-mortem (the two together are what let us pin the deferred-remove
        ``DuplicateIds`` race).
        """
        # Always log the traceback, even on a duplicate context, so a repeating
        # failure leaves a full trail.
        with suppress(Exception):
            self.log.error(
                f"Extension component failed ({context}):\n"
                + "".join(traceback.format_exception(type(error), error, error.__traceback__))
            )
        if context in self._extension_component_failures_reported:
            return
        self._extension_component_failures_reported.add(context)
        if notify:
            summary = f"{type(error).__name__}: {error}"
            if len(summary) > 120:
                summary = summary[:117] + "..."
            self._notify(
                f"An extension component failed ({context}) and was removed ({summary}).",
                severity="error",
            )

    def _follow_transcript_output(self) -> None:
        """Put the transcript back in follow mode for explicit user actions."""
        if not self.screen_stack:
            return
        with suppress(NoMatches):
            self.query_one("#transcript", TranscriptView).follow_output()

    async def _run_terminal_command(self, command: str, *, add_to_context: bool) -> None:
        run_terminal_command = getattr(self.session, "run_terminal_command", None)
        if not callable(run_terminal_command):
            self._notify("Terminal commands are not available.", severity="error")
            return

        item_index = len(self.state.items)
        self.state.add_item(
            "tool",
            f"$ {command.strip()}",
            always_show_tool_result=True,
        )
        item = self.state.items[item_index]
        self._follow_transcript_output()
        transcript = self.query_one("#transcript", TranscriptView)
        await transcript.append_item(
            item,
            theme=self.tui_settings.resolved_theme,
            show_tool_results=True,
            scroll_end=True,
        )
        self._refresh_chrome()

        try:
            result = await run_terminal_command(command, add_to_context=add_to_context)
        except Exception as exc:  # noqa: BLE001 - surface command execution failures in the TUI
            if item_index < len(self.state.items):
                item = self.state.items[item_index]
                item.tool_result_text = format_terminal_command_result_block(
                    ok=False,
                    added_to_context=add_to_context,
                    output=str(exc),
                )
            self._notify(f"Could not run command: {exc}", severity="error")
            await transcript.update_item(
                item,
                theme=self.tui_settings.resolved_theme,
                show_tool_results=True,
            )
            self._refresh_chrome()
            return

        if item_index >= len(self.state.items):
            return
        item = self.state.items[item_index]
        item.text = f"$ {result.command}"
        item.tool_result_text = format_terminal_command_result_block(
            ok=result.ok,
            added_to_context=result.added_to_context,
            output=result.output,
        )
        self._follow_transcript_output()
        await transcript.update_item(
            item,
            theme=self.tui_settings.resolved_theme,
            show_tool_results=True,
        )
        self._refresh_chrome()

    def _replace_tui_settings(self, *, theme: TuiThemeName) -> None:
        """Replace the current immutable TUI settings with a new theme."""
        self.tui_settings = TuiSettings(
            keybindings=self.tui_settings.keybindings,
            theme=theme,
            auto_copy_selection=self.tui_settings.auto_copy_selection,
            sidebar_position=self.tui_settings.sidebar_position,
        )

    def _set_tui_theme(self, theme: TuiThemeName) -> None:
        if theme not in available_tui_theme_names():
            self._notify(f"Unknown theme: {theme}", severity="error")
            return
        self._replace_tui_settings(theme=theme)
        save_tui_settings(self.tui_settings)
        self.theme = theme
        self._refresh()

    async def _queue_prompt(
        self,
        text: str,
        *,
        streaming_behavior: Literal["steer", "follow_up"],
    ) -> None:
        """Queue a prompt for the active agent worker."""
        try:
            async for event in self.session.prompt(text, streaming_behavior=streaming_behavior):
                self.adapter.apply(event)
        except Exception as exc:  # noqa: BLE001 - surface queueing failures in the TUI
            self._notify(f"Could not queue message: {exc}", severity="error")
            return
        self._refresh_chrome()

    async def _run_prompt(
        self,
        text: str,
        run_id: int | None = None,
        *,
        source: Literal["interactive", "extension"] = "interactive",
        custom_type: str | None = None,
        details: dict[str, JSONValue] | None = None,
    ) -> None:
        """Run one prompt and stream session events into the TUI state."""
        active_run_id = self._prompt_run_id if run_id is None else run_id
        try:
            async for event in self.session.prompt(
                text, source=source, custom_type=custom_type, details=details
            ):
                if active_run_id != self._prompt_run_id:
                    return
                if self._consume_optimistic_user_event(event, run_id=active_run_id):
                    self._sync_text_selection_state()
                    self._refresh_chrome()
                    continue
                if self._replace_transformed_optimistic_user_message(event, run_id=active_run_id):
                    self._sync_text_selection_state()
                    continue
                if not (_is_user_message_end_event(event) and self.screen_stack):
                    self.adapter.apply(event)
                self._sync_text_selection_state()
                if (
                    isinstance(event, MessageEndEvent)
                    and isinstance(event.message, AssistantMessage)
                    and event.message.stop_reason == "error"
                ):
                    _attach_diagnostic_log_path_to_error(self.state, self.session)
                await self._apply_streaming_transcript_event(event)
        except Exception as exc:  # noqa: BLE001 - surface unexpected worker errors in the TUI
            if active_run_id != self._prompt_run_id:
                return
            message = _format_prompt_error(exc, self.session)
            self.state.error = message
            self.state.add_item("error", message)
            self.state.running = False
            self._sync_text_selection_state()
            self._refresh()
        finally:
            self._clear_optimistic_user_messages(run_id=active_run_id)
            if active_run_id == self._prompt_run_id:
                self._prompt_worker = None

    async def _apply_streaming_transcript_event(self, event: CodingSessionEvent) -> None:
        """Apply an agent event to mounted transcript widgets without full redraws."""
        if not self.screen_stack:
            self._refresh()
            return
        theme = self.tui_settings.resolved_theme
        try:
            transcript = self.query_one("#transcript", TranscriptView)
        except NoMatches:
            self._refresh()
            return
        if isinstance(event, AgentStartEvent):
            self._refresh_chrome()
            return
        if isinstance(event, AgentEndEvent):
            await transcript.finish_assistant_message()
            self._refresh_chrome()
            return
        if isinstance(event, MessageStartEvent):
            return
        if isinstance(event, MessageUpdateEvent):
            nested = event.assistant_message_event
            if isinstance(nested, TextDeltaEvent):
                await transcript.append_assistant_delta(nested.delta, theme=theme)
            elif isinstance(nested, ThinkingDeltaEvent):
                await transcript.append_thinking_delta(
                    nested.delta,
                    theme=theme,
                    show_thinking=self.state.show_thinking,
                )
            return
        if isinstance(event, MessageEndEvent):
            if isinstance(event.message, (UserMessage, CustomMessage)):
                await self._append_confirmed_user_message(event.message)
                self._sync_session_title()
                return
            if isinstance(event.message, AssistantMessage):
                if event.message.stop_reason in {"error", "aborted"}:
                    # The adapter projected any partial response plus the error
                    # into canonical display state. Rebuild once at this terminal
                    # boundary so the mounted transcript cannot drop the error.
                    self._refresh()
                    return
                visible_blocks = [
                    block
                    for block in event.message.content
                    if (
                        isinstance(block, TextContent)
                        and bool(block.text)
                        or isinstance(block, ThinkingContent)
                        and bool(block.thinking)
                    )
                ]
                canonical_items = self.state.items[-len(visible_blocks) :] if visible_blocks else []
                if (
                    any(isinstance(block, ThinkingContent) for block in visible_blocks)
                    or len(visible_blocks) > 1
                ):
                    # Replace only this message's provisional streaming widgets;
                    # unrelated history remains mounted and selectable.
                    await transcript.finish_structured_assistant_message(
                        canonical_items,
                        theme=theme,
                        show_thinking=self.state.show_thinking,
                    )
                else:
                    canonical_item = canonical_items[-1] if canonical_items else None
                    await transcript.finish_assistant_message(
                        event.message.text,
                        item=canonical_item,
                    )
                self._refresh_chrome()
                return
            return
        if isinstance(event, ToolExecutionStartEvent):
            await transcript.finish_assistant_message()
            item = self.state.items[-1]
            await transcript.append_item(
                item,
                theme=theme,
                show_tool_results=self.state.show_tool_results,
                invocation=self.state.resolve_tool_invocation(item),
            )
            self._refresh_chrome()
            return
        if isinstance(event, ToolExecutionUpdateEvent):
            await transcript.finish_assistant_message()
            updated_item = self.state.find_tool_item(event.tool_call_id)
            if updated_item is not None:
                expanded = self.state.show_tool_results or updated_item.always_show_tool_result
                await transcript.update_item(
                    updated_item,
                    theme=theme,
                    show_tool_results=expanded,
                    invocation=self.state.resolve_tool_invocation(updated_item),
                    result_markup=self.state.resolve_tool_result(updated_item, expanded=expanded),
                )
            self._refresh_chrome()
            return
        if isinstance(event, AutoRetryStartEvent):
            await transcript.finish_assistant_message()
            if self.state.items:
                await transcript.append_item(
                    self.state.items[-1],
                    theme=theme,
                    show_tool_results=self.state.show_tool_results,
                )
            self._refresh_chrome()
            return
        if isinstance(event, ToolExecutionEndEvent):
            updated_item = self.state.find_tool_item(event.tool_call_id)
            if updated_item is not None:
                expanded = self.state.show_tool_results or updated_item.always_show_tool_result
                await transcript.update_item(
                    updated_item,
                    theme=theme,
                    show_tool_results=expanded,
                    invocation=self.state.resolve_tool_invocation(updated_item),
                    result_markup=self.state.resolve_tool_result(updated_item, expanded=expanded),
                )
            self._refresh_chrome()
            return
        if isinstance(event, QueueUpdateEvent):
            self._refresh_chrome()
            return
        self._refresh_chrome()

    def action_cancel(self) -> None:
        """Cancel the active compaction or agent turn."""
        if self._cancel_active_compaction(notify=True):
            return
        self._cancel_active_prompt(notify=True)

    def _cancel_active_compaction(self, *, notify: bool) -> bool:
        """Cancel the active manual compaction worker and restore visible session state."""
        worker = self._compaction_worker
        if worker is None or worker.is_finished or worker.is_cancelled:
            return False

        worker.cancel()
        self._compaction_worker = None
        self.state.clear()
        self.state.set_skills(self.session.skills)
        self._load_session_messages_from_session()
        self._refresh()
        if notify:
            self._notify("Cancelled compaction.")
        return True

    def _cancel_active_prompt(self, *, notify: bool, interrupt: bool = False) -> None:
        """Cancel the active prompt worker and ignore any late events from it."""
        del interrupt
        worker = self._prompt_worker
        is_worker_active = worker is not None and not worker.is_cancelled
        is_session_running = bool(getattr(self.session, "is_running", False))
        if not (self.state.running or is_session_running or is_worker_active):
            return

        self._prompt_run_id += 1
        cancel = getattr(self.session, "cancel", None)
        if callable(cancel):
            cancel()
        if worker is not None and not worker.is_cancelled:
            worker.cancel()
        self._prompt_worker = None
        self.state.running = False
        self.state.assistant_buffer = ""
        self._sync_text_selection_state()
        self._refresh()
        if notify:
            self._notify("Interrupted current operation.")

    def action_accept_completion(self) -> None:
        """Accept the currently selected prompt completion."""
        if isinstance(self.screen, ModelPickerScreen):
            self.screen.action_toggle_mode()
            return
        if isinstance(
            self.screen,
            SessionPickerScreen
            | TreePickerScreen
            | LoginMethodPickerScreen
            | LoginProviderPickerScreen
            | ThemePickerScreen
            | ExtensionSelectScreen
            | ExtensionConfirmScreen,
        ):
            self.screen.action_select_cursor()
            return
        prompt = self.query_one("#prompt", PromptInput)
        applied = self._apply_selected_completion(prompt.text)
        if applied is None:
            return
        prompt.text = applied
        prompt.move_cursor(_text_end_location(applied))
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh_completions()

    def action_completion_next(self) -> None:
        """Select the next prompt completion or move down in the prompt."""
        if isinstance(self.screen, CommandOutputScreen):
            self.screen.action_scroll_down()
            return
        if isinstance(
            self.screen,
            SessionPickerScreen
            | TreePickerScreen
            | LoginMethodPickerScreen
            | LoginProviderPickerScreen
            | ThemePickerScreen
            | ModelPickerScreen
            | ExtensionSelectScreen
            | ExtensionConfirmScreen,
        ):
            self.screen.action_cursor_down()
            return
        if not self._completion_state.items:
            self.query_one("#prompt", PromptInput).action_cursor_down()
            return
        self._completion_state = self._completion_state.select_next()
        self._refresh_completions()

    def action_completion_previous(self) -> None:
        """Select the previous prompt completion or move up in the prompt."""
        if isinstance(self.screen, CommandOutputScreen):
            self.screen.action_scroll_up()
            return
        if isinstance(
            self.screen,
            SessionPickerScreen
            | TreePickerScreen
            | LoginMethodPickerScreen
            | LoginProviderPickerScreen
            | ThemePickerScreen
            | ModelPickerScreen
            | ExtensionSelectScreen
            | ExtensionConfirmScreen,
        ):
            self.screen.action_cursor_up()
            return
        if not self._completion_state.items:
            if self.action_edit_queued_message():
                return
            if self.action_recall_previous_prompt():
                return
            self.query_one("#prompt", PromptInput).action_cursor_up()
            return
        self._completion_state = self._completion_state.select_previous()
        self._refresh_completions()

    def action_recall_previous_prompt(self) -> bool:
        """Recall the most recent submitted prompt into an empty prompt input."""
        prompt = self.query_one("#prompt", PromptInput)
        # Only recall into an empty input so an accidental Up press does not
        # erase a prompt the user is still writing.
        if prompt.text.strip() or not self._prompt_history:
            return False
        previous_prompt = self._prompt_history[-1]
        prompt.text = previous_prompt
        prompt.move_cursor(_text_end_location(previous_prompt))
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh_completions()
        return True

    def action_edit_queued_message(self) -> bool:
        """Move the latest queued message back into the prompt for editing."""
        if not self.state.running:
            return False
        prompt = self.query_one("#prompt", PromptInput)
        if prompt.text.strip():
            return False

        message = self._pop_latest_queued_message()
        if not message:
            return False
        prompt.text = message
        prompt.move_cursor(_text_end_location(message))
        self._sync_queue_state()
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh()
        return True

    def action_edit_queued_follow_up(self) -> bool:
        """Move the latest queued message back into the prompt for editing."""
        return self.action_edit_queued_message()

    def _pop_latest_queued_message(self) -> str | None:
        """Pop the latest queued follow-up or steering message from the session."""
        pop_follow_up = getattr(self.session, "pop_latest_follow_up_message", None)
        if callable(pop_follow_up):
            message = pop_follow_up()
            if isinstance(message, str) and message:
                return message

        pop_steering = getattr(self.session, "pop_latest_steering_message", None)
        if callable(pop_steering):
            message = pop_steering()
            if isinstance(message, str) and message:
                return message

        return None

    def action_open_command_palette(self) -> None:
        """Open the slash-command palette in the prompt."""
        prompt = self.query_one("#prompt", PromptInput)
        prompt.focus()
        prompt.text = "/"
        prompt.move_cursor((0, 1))
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh_completions()

    def action_open_session_picker(self) -> None:
        """Open the indexed session picker."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        records = _session_records(self.session)
        if not records:
            self._notify("No sessions found.")
            return
        self.push_screen(
            SessionPickerScreen(records, theme=self.tui_settings.resolved_theme),
            callback=self._handle_session_picker_result,
        )

    def action_cycle_thinking(self) -> None:
        """Cycle the active thinking mode."""
        self.run_worker(self._cycle_thinking_level(), exclusive=False)

    def action_cycle_model(self) -> None:
        """Cycle through scoped models."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        self.run_worker(self._cycle_scoped_model(), exclusive=False)

    def action_toggle_tool_results(self) -> None:
        """Toggle inline tool result details without rebuilding unrelated history."""
        expanded = self.state.toggle_tool_results()
        self.run_worker(self._update_tool_results_visibility(), exclusive=False)
        self._notify("Tool results expanded." if expanded else "Tool results collapsed.")

    async def _update_tool_results_visibility(self) -> None:
        transcript = self.query_one("#transcript", TranscriptView)
        await transcript.update_tool_results_visibility(
            self.state,
            theme=self.tui_settings.resolved_theme,
        )

    def action_toggle_thinking(self) -> None:
        """Toggle thinking-token display in the transcript."""
        self.state.toggle_thinking()
        transcript = self.query_one("#transcript", TranscriptView)
        transcript.update_thinking_visibility(
            self.state,
            theme=self.tui_settings.resolved_theme,
        )

    def _handle_session_picker_result(self, session_id: str | None) -> None:
        if session_id is None:
            return
        self.run_worker(self._resume_session(session_id), exclusive=False)

    async def _resume_session(self, session_id: str) -> None:
        try:
            resume_message = await self.session.resume(session_id)
            self.state.clear()
            self.state.set_skills(self.session.skills)
            self._load_session_messages_from_session()
            self._notify(resume_message)
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
        self._refresh()

    async def _open_tree_picker(self) -> None:
        if self._is_agent_or_queue_active():
            self._notify(TREE_RUNNING_MESSAGE, severity="warning")
            return
        tree_choices = getattr(self.session, "tree_choices", None)
        if tree_choices is None:
            self._notify("Session tree is not available.", severity="warning")
            return
        try:
            choices = tuple(await tree_choices())
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
            return
        if not choices:
            self._notify("No session entries are available for branching.", severity="warning")
            return
        self.push_screen(
            TreePickerScreen(choices, theme=self.tui_settings.resolved_theme),
            callback=self._handle_tree_picker_result,
        )

    def _handle_tree_picker_result(self, result: TreePickerResult | None) -> None:
        if result is None:
            return
        self.run_worker(
            self._branch_to_tree_entry(
                result.entry_id,
                summarize=result.summarize,
                custom_instructions=result.custom_instructions,
            ),
            exclusive=False,
        )

    async def _branch_to_tree_entry(
        self,
        entry_id: str,
        *,
        summarize: bool,
        custom_instructions: str | None = None,
    ) -> None:
        if self._is_agent_or_queue_active():
            self._notify(TREE_RUNNING_MESSAGE, severity="warning")
            return
        branch_to_entry = getattr(self.session, "branch_to_entry", None)
        if branch_to_entry is None:
            self._notify("Session tree is not available.", severity="warning")
            return
        try:
            if summarize:
                self.state.clear()
                self.state.add_item("status", "Summarizing branch…")
                self._refresh()

            result = branch_to_entry(
                entry_id,
                summarize=summarize,
                custom_instructions=custom_instructions,
            )
            if isawaitable(result):
                result = await result
            self.state.clear()
            self.state.set_skills(self.session.skills)
            self._load_session_messages_from_session()
            if isinstance(result, SessionTreeBranchResult):
                if result.input_prefill is not None:
                    prompt = self.query_one("#prompt", PromptInput)
                    prompt.value = result.input_prefill
                    prompt.move_cursor(_text_end_location(result.input_prefill))
                    prompt.focus()
                self._notify(result.message)
            elif isinstance(result, str):
                self._notify(result)
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
        self._refresh()

    async def _new_session(self) -> None:
        self._cancel_active_prompt(notify=False, interrupt=True)
        new_session = getattr(self.session, "new_session", None)
        if new_session is None:
            self._notify("Session manager is not available.")
            return
        try:
            await new_session()
            self.state.clear()
            self.state.set_skills(self.session.skills)
            self._load_session_messages_from_session()
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
        self._refresh()

    def _apply_selected_completion(self, value: str) -> str | None:
        item = self._completion_state.selected
        if item is None:
            return None
        return item.apply(value)

    def _append_command_message(self, command_text: str, message: str) -> None:
        """Append non-persistent command output to the visible transcript."""
        self.state.add_item("status", f"{_command_output_title(command_text)}\n{message}")

    def _show_command_message(self, command_text: str, message: str) -> None:
        self.push_screen(
            CommandOutputScreen(
                _command_output_title(command_text),
                message,
                theme=self.tui_settings.resolved_theme,
                auto_copy_selection=command_text.strip().split(maxsplit=1)[0] == "/session",
            )
        )

    def _open_login_picker(self) -> None:
        self.push_screen(
            LoginMethodPickerScreen(theme=self.tui_settings.resolved_theme),
            callback=self._handle_login_method_result,
        )

    def _handle_login_method_result(self, method: str | None) -> None:
        if method is None:
            return
        if method == "subscription":
            providers = _subscription_login_providers(BUILTIN_PROVIDER_CATALOG)
        elif method == "api-key":
            providers = _api_key_login_providers(BUILTIN_PROVIDER_CATALOG)
        elif method == "custom":
            self._open_custom_provider_login()
            return
        else:
            self._notify(f"Unknown login method: {method}", severity="error")
            return
        if not providers:
            self._notify("No login providers are available for that method.", severity="warning")
            return
        self.push_screen(
            LoginProviderPickerScreen(
                providers,
                theme=self.tui_settings.resolved_theme,
                back_on_cancel=True,
            ),
            callback=lambda provider_name: self._handle_login_provider_result(
                provider_name,
                method=method,
            ),
        )

    def _handle_login_provider_result(
        self,
        provider_name: str | _LoginFlowAction | None,
        *,
        method: str | None = None,
    ) -> None:
        if provider_name is _LoginFlowAction.BACK:
            self._open_login_picker()
        elif provider_name is not None:
            self._open_login(provider_name, method=method)

    def _open_custom_provider_login(self) -> None:
        self.push_screen(
            CustomProviderLoginScreen(theme=self.tui_settings.resolved_theme),
            callback=self._handle_custom_provider_login_result,
        )

    def _handle_custom_provider_login_result(
        self,
        result: CustomProviderLoginResult | _LoginFlowAction | None,
    ) -> None:
        if result is _LoginFlowAction.BACK:
            self._open_login_picker()
            return
        if result is None:
            return
        provider = OpenAICompatibleProviderConfig(
            name=result.provider_name,
            base_url=result.base_url.rstrip("/"),
            api_key_env=result.api_key_env,
            credential_name=result.provider_name,
            models=result.models,
            default_model=result.default_model,
        )
        catalog_entry = ProviderCatalogEntry(
            name=provider.name,
            display_name=result.display_name,
            kind="openai-compatible",
            base_url=provider.base_url,
            api_key_env=provider.api_key_env,
            credential_name=provider.credential_name,
            models=provider.models,
            default_model=provider.default_model,
            docs_url=provider.base_url,
        )
        try:
            save_user_catalog_entries((catalog_entry,))
            FileCredentialStore().set(provider.credential_name or provider.name, result.api_key)
            settings = load_provider_settings()
            updated = upsert_openai_compatible_provider(settings, provider, set_default=False)
            save_provider_settings(updated)
            self.session.reload_provider_settings()
            try:
                self.session.set_provider(provider.name, persist_default=False)
            except TypeError:
                self.session.set_provider(provider.name)
        except Exception as exc:  # noqa: BLE001 - surface login failures in the TUI
            self._notify(f"Could not save custom provider: {exc}", severity="error")
            return
        self._notify(f"Saved custom provider {result.display_name}.")
        self._refresh()

    def _open_login(self, provider_name: str, *, method: str | None = None) -> None:
        entry = builtin_provider_entry(provider_name)
        if entry is None:
            self._notify(f"Unknown provider: {provider_name}", severity="error")
            return
        use_oauth = method == "subscription" or (
            method is None and "api_key" not in entry.auth_methods
        )
        if use_oauth and get_oauth_provider(entry.name) is not None:
            login = None
            if entry.name == "openai-codex":

                async def login(callbacks: OAuthLoginCallbacks) -> OAuthCredential:
                    return await login_openai_codex(
                        on_auth=callbacks.on_auth,
                        on_prompt=callbacks.on_prompt,
                        on_manual_code_input=callbacks.on_manual_code_input,
                        on_progress=callbacks.on_progress,
                    )

            self.push_screen(
                OAuthLoginScreen(
                    entry,
                    theme=self.tui_settings.resolved_theme,
                    login=login,
                ),
                callback=lambda credential: self._handle_oauth_login_navigation_result(
                    entry, credential
                ),
            )
            return
        self.push_screen(
            LoginScreen(entry, theme=self.tui_settings.resolved_theme),
            callback=lambda api_key: self._handle_api_key_login_navigation_result(entry, api_key),
        )

    def _handle_api_key_login_navigation_result(
        self,
        entry: ProviderCatalogEntry,
        result: str | _LoginFlowAction | None,
    ) -> None:
        if result is _LoginFlowAction.BACK:
            self._open_login_picker()
        else:
            self._handle_login_result(entry, result)

    def _handle_login_result(self, entry: ProviderCatalogEntry, api_key: str | None) -> None:
        if api_key is None:
            return
        if entry.credential_name is None:
            self._notify(
                f"Provider {entry.name} does not support saved credentials.",
                severity="error",
            )
            return
        try:
            FileCredentialStore().set(entry.credential_name, api_key)
            provider = provider_config_from_catalog_entry(entry.name)
            upsert_saved_provider(provider, set_default=False)
            self.session.reload_provider_settings()
            try:
                self.session.set_provider(entry.name, persist_default=False)
            except TypeError:
                self.session.set_provider(entry.name)
        except Exception as exc:  # noqa: BLE001 - surface login failures in the TUI
            self._notify(f"Could not save login: {exc}", severity="error")
            return
        self._notify(f"Saved login for {entry.display_name}.")
        self._refresh()

    def _handle_oauth_login_navigation_result(
        self,
        entry: ProviderCatalogEntry,
        result: OAuthCredential | _LoginFlowAction | None,
    ) -> None:
        if result is _LoginFlowAction.BACK:
            self._open_login_picker()
        else:
            self._handle_oauth_login_result(entry, result)

    def _handle_oauth_login_result(
        self,
        entry: ProviderCatalogEntry,
        credential: OAuthCredential | None,
    ) -> None:
        if credential is None:
            return
        if entry.credential_name is None:
            self._notify(
                f"Provider {entry.name} does not support saved credentials.",
                severity="error",
            )
            return
        try:
            FileCredentialStore().set_oauth(entry.credential_name, credential)
            provider = provider_config_from_catalog_entry(entry.name)
            upsert_saved_provider(provider, set_default=False)
            self.session.reload_provider_settings()
            try:
                self.session.set_provider(entry.name, persist_default=False)
            except TypeError:
                self.session.set_provider(entry.name)
        except Exception as exc:  # noqa: BLE001 - surface login failures in the TUI
            self._notify(f"Could not save login: {exc}", severity="error")
            return
        self._notify(f"Saved login for {entry.display_name}.")
        self._refresh()

    def _open_logout_picker(self) -> None:
        providers = _stored_credential_providers(BUILTIN_PROVIDER_CATALOG)
        if not providers:
            self._notify(NO_STORED_CREDENTIALS_MESSAGE, severity="warning")
            return
        self.push_screen(
            LoginProviderPickerScreen(
                providers,
                theme=self.tui_settings.resolved_theme,
                title="Logout",
            ),
            callback=self._handle_logout_provider_result,
        )

    def _handle_logout_provider_result(self, provider_name: str | _LoginFlowAction | None) -> None:
        if isinstance(provider_name, str):
            self._logout(provider_name)

    def _logout(self, provider_name: str) -> None:
        entry = builtin_provider_entry(provider_name)
        if entry is None:
            self._notify(f"Unknown provider: {provider_name}", severity="error")
            return

        if entry.credential_name is None:
            self._notify(NO_STORED_CREDENTIALS_MESSAGE, severity="warning")
            return
        credential_store = FileCredentialStore()
        if not _credential_store_has_entry(credential_store, entry.credential_name):
            self._notify(NO_STORED_CREDENTIALS_MESSAGE, severity="warning")
            return

        try:
            credential_store.delete(entry.credential_name)
            self.session.reload_provider_settings()
        except Exception as exc:  # noqa: BLE001 - surface logout failures in the TUI
            self._notify(f"Could not log out: {exc}", severity="error")
            return

        if entry.kind == "openai-codex":
            self._notify(f"Logged out of {entry.display_name}.")
        else:
            self._notify(
                f"Removed stored API key for {entry.display_name}. "
                "Environment variables and providers.json config are unchanged."
            )
        self._refresh()

    def _available_model_choices(self) -> tuple[ModelChoice, ...]:
        fallback_choices = (
            ModelChoice(provider_name=self.session.provider_name, model=model)
            for model in self.session.available_models
        )
        return tuple(
            getattr(
                self.session,
                "available_model_choices",
                fallback_choices,
            )
        )

    def _open_model_picker(self) -> None:
        choices = self._available_model_choices()
        if not choices:
            self._notify(
                "No configured providers are usable. Run /login to set up a provider.",
                severity="warning",
            )
            return
        self.push_screen(
            ModelPickerScreen(
                choices,
                scoped_choices=tuple(getattr(self.session, "scoped_model_choices", ())),
                current_model=self.session.model,
                provider_name=self.session.provider_name,
                theme=self.tui_settings.resolved_theme,
                on_toggle_scoped=None,
                picker_kind="model",
            ),
            callback=self._handle_model_picker_result,
        )

    def _open_scoped_models_picker(self) -> None:
        choices = self._available_model_choices()
        if not choices:
            self._notify(
                "No configured providers are usable. Run /login to set up a provider.",
                severity="warning",
            )
            return
        self.push_screen(
            ModelPickerScreen(
                choices,
                scoped_choices=tuple(getattr(self.session, "scoped_model_choices", ())),
                current_model=self.session.model,
                provider_name=self.session.provider_name,
                theme=self.tui_settings.resolved_theme,
                on_toggle_scoped=self._toggle_scoped_model,
                picker_kind="scoped",
            ),
            callback=self._handle_scoped_models_picker_result,
        )

    def _toggle_scoped_model(self, choice: ModelChoice) -> Sequence[ModelChoice]:
        toggle_scoped_model = getattr(self.session, "toggle_scoped_model", None)
        if toggle_scoped_model is None:
            self._notify("Scoped model controls are not available.", severity="warning")
            return tuple(getattr(self.session, "scoped_model_choices", ()))
        try:
            return tuple(toggle_scoped_model(choice))
        except Exception as exc:  # noqa: BLE001 - surface session state failures in the TUI
            self._notify(f"Could not update scoped models: {exc}", severity="error")
            return tuple(getattr(self.session, "scoped_model_choices", ()))

    def _handle_scoped_models_picker_result(self, choice: ModelChoice | None) -> None:
        del choice
        self._refresh_chrome()

    def _handle_model_picker_result(self, choice: ModelChoice | None) -> None:
        if choice is None:
            return
        try:
            set_model_choice = getattr(self.session, "set_model_choice", None)
            if set_model_choice is None:
                if choice.provider_name != self.session.provider_name:
                    self.session.set_provider(choice.provider_name)
                self.session.set_model(choice.model)
            else:
                set_model_choice(choice)
        except Exception as exc:  # noqa: BLE001 - surface model switch failures in the TUI
            self._notify(f"Could not switch model: {exc}", severity="error")
            return
        self._refresh_chrome()

    def _open_theme_picker(self) -> None:
        self.push_screen(
            ThemePickerScreen(
                current_theme=self.tui_settings.theme,
                theme=self.tui_settings.resolved_theme,
                theme_names=available_tui_theme_names(),
            ),
            callback=self._handle_theme_picker_result,
        )

    def _handle_theme_picker_result(self, theme: TuiThemeName | None) -> None:
        if theme is None:
            return
        self._set_tui_theme(theme)

    async def _set_thinking_level(self, level: str) -> None:
        setter = getattr(self.session, "set_thinking_level", None)
        if setter is None:
            self._notify("Thinking controls are not available.", severity="warning")
            return
        try:
            result = setter(level)
            if isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - surface session state failures in the TUI
            self._notify(f"Could not change thinking mode: {exc}", severity="error")
            return
        self._refresh_chrome()

    async def _cycle_thinking_level(self) -> None:
        cycler = getattr(self.session, "cycle_thinking_level", None)
        if cycler is None:
            self._notify("Thinking controls are not available.", severity="warning")
            return
        try:
            result = cycler()
            if isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - surface session state failures in the TUI
            self._notify(f"Could not change thinking mode: {exc}", severity="error")
            return
        self._refresh_chrome()

    async def _cycle_scoped_model(self) -> None:
        cycler = getattr(self.session, "cycle_scoped_model", None)
        if cycler is None:
            self._notify("Scoped model controls are not available.", severity="warning")
            return
        try:
            result = cycler()
            if isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 - surface session state failures in the TUI
            self._notify(f"Could not switch scoped model: {exc}", severity="error")
            return
        self._refresh_chrome()

    def _notify(
        self,
        message: str,
        *,
        severity: Literal["information", "warning", "error"] = "information",
    ) -> None:
        key = (message, severity)
        if key in self._active_notification_keys:
            return
        self._active_notification_keys.add(key)
        self.set_timer(
            self.NOTIFICATION_TIMEOUT,
            lambda: self._active_notification_keys.discard(key),
            name=f"notification-dedupe-{hash(key)}",
        )
        self.notify(message, severity=severity, markup=False)

    def _refresh(self) -> None:
        theme = self.tui_settings.resolved_theme
        self._refresh_chrome(theme=theme)
        transcript = self.query_one("#transcript", TranscriptView)
        transcript.update_from_state(self.state, theme=theme)

    def _refresh_chrome(self, *, theme: TuiTheme | None = None) -> None:
        """Refresh non-transcript chrome without remounting transcript blocks."""
        theme = theme or self.tui_settings.resolved_theme
        self._sync_session_title()
        self._sync_text_selection_state()
        self._sync_queue_state()
        sidebar = self.query_one("#sidebar", SessionSidebar)
        sidebar.update_from_session(self.session, theme=theme)
        compact_info = self.query_one("#compact-session-info", CompactSessionInfo)
        compact_info.update_from_session(self.session, theme=theme)
        queued_messages = self.query_one("#queued-messages", Static)
        queue_render_key = (
            self.state.queued_steering,
            self.state.queued_follow_up,
            theme.name,
            theme.muted_text,
        )
        if queue_render_key != self._last_queue_render_key:
            self._last_queue_render_key = queue_render_key
            queued_messages.display = self.state.queued_message_count > 0
            queued_messages.update(_render_queued_messages(self.state, theme=theme))
        self._sync_activity_indicator()
        self._refresh_footer_bindings()

    def _sync_queue_state(self) -> None:
        queue_event = getattr(self.session, "queue_update_event", None)
        if not callable(queue_event):
            return
        self.adapter.apply(queue_event())

    def _sync_activity_indicator(self) -> None:
        self._sync_terminal_title()
        if self.state.running:
            if self._activity_timer is None:
                self._activity_timer = self.set_interval(
                    ACTIVITY_TICK_SECONDS,
                    self._tick_activity,
                    name="activity-indicator",
                )
            else:
                self._activity_timer.resume()
            self._apply_activity_indicator()
            return
        self._activity_frame = 0
        if self._activity_timer is not None:
            self._activity_timer.pause()
        self._apply_activity_indicator()

    def _tick_activity(self) -> None:
        if not self.state.running:
            return
        self._activity_frame += 1
        self._apply_activity_indicator()
        self._sync_terminal_title()
        now = asyncio.get_running_loop().time()
        if now - self._last_tool_timer_refresh_at >= 1.0:
            self._last_tool_timer_refresh_at = now
            self.call_later(self._refresh_pending_tool_timer)

    async def _refresh_pending_tool_timer(self) -> None:
        """Refresh elapsed time on the tool row that is currently executing."""
        if not self.state.running:
            return
        item = next(
            (
                candidate
                for candidate in reversed(self.state.items)
                if candidate.role == "tool" and candidate.tool_result_text is None
            ),
            None,
        )
        if item is None:
            return
        try:
            transcript = self.query_one("#transcript", TranscriptView)
        except NoMatches:
            return
        expanded = self.state.show_tool_results or item.always_show_tool_result
        await transcript.update_item(
            item,
            theme=self.tui_settings.resolved_theme,
            show_tool_results=expanded,
            invocation=self.state.resolve_tool_invocation(item),
            result_markup=self.state.resolve_tool_result(item, expanded=expanded),
        )

    def _apply_activity_indicator(self) -> None:
        theme = self.tui_settings.resolved_theme
        try:
            prompt = self.query_one("#prompt", PromptInput)
            prompt_prefix = self.query_one("#prompt-prefix", Static)
        except NoMatches:
            return
        shell_mode = _is_terminal_command_prompt(prompt.text)
        render_key = (
            theme.name,
            theme.accent,
            theme.screen_background,
            theme.prompt_border,
            self._activity_frame,
            self.state.running,
            shell_mode,
        )
        if render_key == self._last_activity_indicator_key:
            return
        self._last_activity_indicator_key = render_key
        prompt.styles.border_left = (
            "tall",
            _activity_prompt_border_color(
                theme,
                frame=self._activity_frame,
                running=self.state.running,
                shell_mode=shell_mode,
            ),
        )
        prompt_prefix.update(
            _render_activity_indicator(
                theme,
                frame=self._activity_frame,
                running=self.state.running,
            ),
            layout=False,
        )

    def _refresh_completions(self) -> None:
        suggestions = self.query_one("#autocomplete", Static)
        suggestions.display = bool(self._completion_state.items)
        if not self._completion_state.items:
            self._completion_visible_line_budget = None
            suggestions.update(
                render_completion_suggestions(
                    CompletionState(),
                    theme=self.tui_settings.resolved_theme,
                )
            )
            self._refresh_footer_bindings()
            return
        max_lines = self._completion_window_line_budget(suggestions)
        suggestions.update(
            render_completion_suggestions(
                _visible_completion_state(
                    self._completion_state,
                    max_lines=max_lines,
                    width=max(suggestions.content_size.width or suggestions.size.width, 1),
                ),
                theme=self.tui_settings.resolved_theme,
            )
        )
        self._refresh_footer_bindings()

    def _completion_window_line_budget(self, suggestions: Static) -> int:
        """Return a stable completion window size for the current suggestion box.

        The autocomplete widget has ``height: auto``. If we used its current
        rendered height as the next render limit unconditionally, selecting an
        item could render fewer rows, which would shrink the widget, which would
        then make the next render limit smaller again. Keep the largest measured
        height for the current completion session so navigation does not feed
        back into progressively smaller boxes.
        """
        measured_limit = _completion_visible_line_limit(suggestions)
        if suggestions.size.height <= 0:
            if self._completion_visible_line_budget is None:
                self._completion_visible_line_budget = self._initial_completion_line_budget()
            return self._completion_visible_line_budget
        self._completion_visible_line_budget = max(
            self._completion_visible_line_budget or measured_limit,
            measured_limit,
        )
        return self._completion_visible_line_budget

    def _initial_completion_line_budget(self) -> int:
        """Estimate the first completion window size before Textual lays it out."""
        terminal_height = self.size.height
        if terminal_height <= 0:
            return COMPLETION_MAX_VISIBLE_LINES

        reserved_rows = COMPLETION_MIN_TRANSCRIPT_LINES + COMPLETION_WIDGET_CHROME_LINES
        for selector in ("#prompt-row", "#compact-session-info", "#queued-messages"):
            with suppress(NoMatches):
                widget = self.query_one(selector)
                if widget.display:
                    reserved_rows += widget.outer_size.height

        available_rows = terminal_height - reserved_rows
        terminal_fraction_rows = max(1, terminal_height // COMPLETION_INITIAL_TERMINAL_FRACTION)
        return max(
            1,
            min(COMPLETION_MAX_VISIBLE_LINES, available_rows, terminal_fraction_rows),
        )

    def _update_responsive_layout(self, width: int, height: int) -> None:
        if self.tui_settings.sidebar_position == "off":
            return
        show_sidebar = width >= SIDEBAR_MIN_WIDTH and height >= SIDEBAR_MIN_HEIGHT
        self.set_class(not show_sidebar, "-hide-sidebar")

    def _apply_sidebar_position(self) -> None:
        """Apply CSS classes for the configured sidebar position."""
        pos = self.tui_settings.sidebar_position
        self.set_class(pos == "right", "-sidebar-right")
        if pos == "off":
            self.add_class("-hide-sidebar")

    def _build_completion_state(self, text: str) -> CompletionState:
        registry = _session_command_registry(self.session)
        return build_completion_state(
            text,
            command_registry=registry,
            skills=self.session.skills,
            prompt_templates=self.session.prompt_templates,
            model_names=self.session.available_models,
            provider_names=(
                *self.session.available_providers,
                *LOGIN_PROVIDER_ALIASES,
            ),
            thinking_levels=getattr(self.session, "available_thinking_levels", ()),
            theme_names=available_tui_theme_names(),
            session_options=_session_options(self.session),
            cwd=self.session.cwd,
        )

    def _refresh_footer_bindings(self) -> None:
        prompt = self.query_one("#prompt", PromptInput)
        prompt.set_footer_mode(_prompt_footer_mode(self.state, self._completion_state))

    def _sync_prompt_shell_mode(self, text: str) -> None:
        prompt = self.query_one("#prompt", PromptInput)
        prompt.shell_mode_style = self.tui_settings.resolved_theme.accent
        prompt.set_class(_is_terminal_command_prompt(text), "-shell-mode")
        prompt.refresh()
        self._apply_activity_indicator()


def _activity_prompt_border_color(
    theme: TuiTheme,
    *,
    frame: int,
    running: bool,
    shell_mode: bool,
) -> str:
    """Return the prompt border color for the current activity animation frame."""
    del frame, running
    if shell_mode:
        return theme.accent
    return theme.prompt_border


def _render_activity_indicator(theme: TuiTheme, *, frame: int, running: bool) -> Text:
    """Render the prompt prefix, turning Tau into a moving square while running."""
    if not running:
        return Text("τ", style=f"bold {theme.accent}")

    cycle_length = (ACTIVITY_INDICATOR_HEIGHT - 1) * 2
    cycle_position = frame % cycle_length
    active_row = (
        cycle_position
        if cycle_position < ACTIVITY_INDICATOR_HEIGHT
        else cycle_length - cycle_position
    )
    direction = 1 if cycle_position < ACTIVITY_INDICATOR_HEIGHT else -1
    trail_rows = {
        active_row: theme.accent,
        active_row - direction: _blend_hex_colors(
            theme.accent,
            theme.screen_background,
            fraction=0.35,
        ),
        active_row - (direction * 2): _blend_hex_colors(
            theme.accent,
            theme.screen_background,
            fraction=0.65,
        ),
    }

    rendered = Text()
    for row in range(ACTIVITY_INDICATOR_HEIGHT):
        color = trail_rows.get(row)
        if color is None:
            rendered.append(" ")
        else:
            rendered.append("■", style=color)
        if row < ACTIVITY_INDICATOR_HEIGHT - 1:
            rendered.append("\n")
    return rendered


def _is_terminal_command_prompt(text: str) -> bool:
    """Return whether the prompt is currently in terminal-command mode."""
    return _terminal_command_prefix_span(text) is not None


def _should_optimistically_render_prompt(text: str) -> bool:
    """Return whether submitted text can be safely shown before session expansion."""
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith("/")


def _is_user_message_end_event(event: CodingSessionEvent) -> bool:
    """Return whether an agent event closes a user-context message."""
    return isinstance(event, MessageEndEvent) and isinstance(
        event.message, (UserMessage, CustomMessage)
    )


def _terminal_command_prefix_span(text: str) -> tuple[int, int] | None:
    """Return the input span for a leading ! or !! terminal-command prefix."""
    leading_whitespace = len(text) - len(text.lstrip())
    stripped = text[leading_whitespace:]
    if stripped.startswith("!!"):
        return (leading_whitespace, leading_whitespace + 2)
    if stripped.startswith("!"):
        return (leading_whitespace, leading_whitespace + 1)
    return None


def _blend_hex_colors(start: str, end: str, *, fraction: float) -> str:
    """Blend two ``#rrggbb`` colors by ``fraction``."""
    start_rgb = _hex_to_rgb(start)
    end_rgb = _hex_to_rgb(end)
    blended = tuple(
        round(start_channel + (end_channel - start_channel) * fraction)
        for start_channel, end_channel in zip(start_rgb, end_rgb, strict=True)
    )
    return f"#{blended[0]:02x}{blended[1]:02x}{blended[2]:02x}"


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.removeprefix("#")
    if len(value) != 6:
        raise ValueError(f"Expected #rrggbb color, got {color!r}")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _completion_visible_line_limit(suggestions: Static) -> int:
    """Return the number of completion render lines that fit in the widget body."""
    if suggestions.size.height > 0:
        return max(min(COMPLETION_MAX_VISIBLE_LINES, suggestions.size.height), 1)
    return COMPLETION_MAX_VISIBLE_LINES


def _visible_completion_state(
    state: CompletionState,
    *,
    max_lines: int,
    width: int | None = None,
) -> CompletionState:
    """Return a completion-state window with the selected item visible."""
    if not state.items or max_lines <= 0:
        return CompletionState()

    selected_line_limit = max(max_lines - 1, 1)
    start = 0
    while start < state.selected_index:
        candidate = CompletionState(
            items=state.items[start:],
            selected_index=state.selected_index - start,
        )
        if _completion_selected_render_line(candidate, width=width) < selected_line_limit:
            break
        start += 1

    end = len(state.items)
    while end > state.selected_index + 1:
        candidate = CompletionState(
            items=state.items[start:end],
            selected_index=state.selected_index - start,
        )
        if _completion_render_line_count(candidate, width=width) <= max_lines:
            break
        end -= 1

    while start < state.selected_index:
        candidate = CompletionState(
            items=state.items[start:end],
            selected_index=state.selected_index - start,
        )
        if _completion_render_line_count(candidate, width=width) <= max_lines:
            break
        start += 1

    return CompletionState(
        items=state.items[start:end],
        selected_index=state.selected_index - start,
    )


def _completion_selected_render_line(state: CompletionState, *, width: int | None = None) -> int:
    """Return the rendered line number for the selected completion item."""
    line = 0
    has_rendered_text = False
    previous_category: str | None = None
    for index, item in enumerate(state.items):
        if item.category != previous_category:
            if has_rendered_text:
                line += 1
            if item.category:
                line += 1
                has_rendered_text = True
            previous_category = item.category
        elif has_rendered_text:
            line += 1
        if index == state.selected_index:
            return line
        line += _completion_item_extra_wrapped_lines(item, width=width)
        has_rendered_text = True
    return line


def _completion_render_line_count(state: CompletionState, *, width: int | None = None) -> int:
    """Return how many lines the completion state renders into."""
    if not state.items:
        return 0
    line_count = 0
    previous_category: str | None = None
    for index, item in enumerate(state.items):
        if item.category != previous_category:
            if index:
                line_count += 1
            if item.category:
                line_count += 1
            previous_category = item.category
        line_count += 1 + _completion_item_extra_wrapped_lines(item, width=width)
    return line_count


def _completion_item_extra_wrapped_lines(
    item: CompletionItem,
    *,
    width: int | None,
) -> int:
    """Return extra rendered lines used when a completion description wraps."""
    if width is None or width <= 0 or not item.description:
        return 0
    output = StringIO()
    console = Console(
        file=output,
        width=width,
        force_terminal=False,
        color_system=None,
        legacy_windows=False,
    )
    console.print(
        render_completion_suggestions(
            CompletionState(items=(item,), selected_index=0),
            theme=TAU_DARK_THEME,
        ),
        end="",
    )
    line_count = len(output.getvalue().splitlines())
    return max(line_count - 1, 0)


def _session_command_registry(session: CodingSession) -> CommandRegistry:
    registry = getattr(session, "command_registry", None)
    if isinstance(registry, CommandRegistry):
        return registry
    return create_default_command_registry()


def _session_options(session: CodingSession) -> tuple[CompletionOption, ...]:
    return tuple(_session_option(record) for record in _session_records(session))


def _session_records(session: CodingSession) -> tuple[SessionCompletionRecord, ...]:
    manager = getattr(session, "session_manager", None)
    if manager is None:
        return ()
    try:
        records = manager.list_sessions(session.cwd)
    except TypeError:
        records = manager.list_sessions()
    return tuple(records)


def _session_option(record: SessionCompletionRecord) -> CompletionOption:
    description_parts = [record.title if record.title else "Untitled session"]
    if record.model:
        description_parts.append(record.model)
    description_parts.append(_short_path(record.cwd))
    return CompletionOption(value=record.id, description=" - ".join(description_parts))


def _short_path(path: Path) -> str:
    home = Path.home()
    try:
        return f"~/{path.relative_to(home)}"
    except ValueError:
        return str(path)


def _session_picker_label(record: SessionCompletionRecord) -> str:
    parts = [_session_updated_at_label(record.updated_at)]
    if record.model:
        parts.append(record.model)
    title = _named_session_title(record.title)
    if title is not None:
        parts.append(title)
    return " - ".join(parts)


def _filter_session_records(
    records: Sequence[SessionCompletionRecord],
    query: str,
) -> tuple[SessionCompletionRecord, ...]:
    normalized = query.strip().casefold()
    if not normalized:
        return tuple(records)
    return tuple(
        record
        for record in records
        if normalized in (record.title or "").casefold() or normalized in record.model.casefold()
    )


def _tree_picker_label(
    choice: SessionTreeChoice,
    *,
    theme: TuiTheme,
    highlighted: bool = False,
) -> Text:
    marker = "* " if choice.active else "  "
    label = choice.label
    indent_width = len(label) - len(label.lstrip(" "))
    indent = label[:indent_width]
    body = label[indent_width:]
    author, separator, rest = body.partition(":")
    text = Text(f"{marker}{indent}")
    if separator:
        author_color = theme.highlight_text if highlighted else theme.accent
        text.append(author, style=author_color)
        text.append(f"{separator}{rest}")
    else:
        text.append(body)
    return text


def _active_tree_choice_index(choices: Sequence[SessionTreeChoice]) -> int:
    return _tree_choice_index(choices, None)


def _tree_choice_index(choices: Sequence[SessionTreeChoice], entry_id: str | None) -> int:
    if entry_id is not None:
        for index, choice in enumerate(choices):
            if choice.entry_id == entry_id:
                return index
    for index, choice in enumerate(choices):
        if choice.active:
            return index
    return 0


def _session_updated_at_label(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _named_session_title(title: str | None) -> str | None:
    if title is None:
        return None
    stripped = title.strip()
    if not stripped or stripped.lower() == "untitled session":
        return None
    return stripped


def _login_provider_label(provider: ProviderCatalogEntry) -> str:
    return f"{provider.display_name} — {provider.name}"


def _subscription_login_providers(
    providers: Sequence[ProviderCatalogEntry],
) -> tuple[ProviderCatalogEntry, ...]:
    provider_ids = oauth_provider_ids()
    return tuple(provider for provider in providers if provider.name in provider_ids)


def _api_key_login_providers(
    providers: Sequence[ProviderCatalogEntry],
) -> tuple[ProviderCatalogEntry, ...]:
    return tuple(provider for provider in providers if "api_key" in provider.auth_methods)


def _stored_credential_providers(
    providers: Sequence[ProviderCatalogEntry],
) -> tuple[ProviderCatalogEntry, ...]:
    credential_store = FileCredentialStore()
    return tuple(
        provider
        for provider in providers
        if provider.credential_name is not None
        and _credential_store_has_entry(credential_store, provider.credential_name)
    )


def _credential_store_has_entry(
    credential_store: FileCredentialStore,
    credential_name: str,
) -> bool:
    return (
        credential_store.get(credential_name) is not None
        or credential_store.get_oauth(credential_name) is not None
    )


def _theme_picker_label(theme_name: TuiThemeName, *, current_theme: TuiThemeName) -> str:
    marker = "✓" if theme_name == current_theme else " "
    return f"{marker} {theme_name}"


def _model_picker_label(
    choice: ModelChoice,
    *,
    current_model: str,
    current_provider: str,
    scoped: bool = False,
) -> str:
    marker = (
        "* "
        if (choice.provider_name == current_provider and choice.model == current_model)
        else "  "
    )
    suffix = " [scoped]" if scoped else ""
    return f"{marker}{choice.provider_name}:{choice.model}{suffix}"


def _filter_login_providers(
    providers: Sequence[ProviderCatalogEntry],
    query: str,
) -> tuple[ProviderCatalogEntry, ...]:
    normalized = query.strip().casefold()
    if not normalized:
        return tuple(providers)
    return tuple(
        provider
        for provider in providers
        if normalized in provider.name.casefold() or normalized in provider.display_name.casefold()
    )


def _filter_model_choices(choices: Sequence[ModelChoice], query: str) -> tuple[ModelChoice, ...]:
    normalized = query.strip().lower()
    if not normalized:
        return tuple(choices)
    return tuple(
        choice
        for choice in choices
        if normalized in choice.provider_name.lower() or normalized in choice.model.lower()
    )


def _command_message_uses_transcript(command_text: str) -> bool:
    """Return whether slash-command output should appear inline in the transcript."""
    command_name = command_text.split(maxsplit=1)[0].casefold()
    return command_name in {"/reload", "/system"}


def _command_message_uses_notification(command_text: str, message: str) -> bool:
    """Return whether slash-command output should appear as a notification."""
    command_name = command_text.split(maxsplit=1)[0].casefold()
    return command_name == "/name" and message.startswith("Session renamed: ")


def _command_output_title(command_text: str) -> str:
    command_name = command_text.split(maxsplit=1)[0].removeprefix("/")
    return f"/{command_name or 'help'}"


def _is_thinking_cycle_key(key: str, configured_key: str) -> bool:
    if key == configured_key:
        return True
    return configured_key == "shift+tab" and key == "backtab"


def _textual_theme_for_tau_theme(theme_name: TuiThemeName) -> Theme:
    """Map a Tau theme to Textual's native theme type."""
    theme = get_tui_theme(theme_name)
    return Theme(
        name=theme.name,
        primary=theme.accent,
        secondary=theme.prompt_border,
        warning=theme.markdown_bullet,
        error=theme.error,
        success=theme.success,
        accent=theme.accent,
        foreground=theme.screen_text,
        background=theme.screen_background,
        surface=theme.chrome_background,
        panel=theme.sidebar_background,
        dark=theme.dark,
        variables=_theme_css_variables(theme),
    )


def _theme_css_variables(theme: TuiTheme) -> dict[str, str]:
    """Return Textual CSS variables for a resolved Tau theme."""
    return {
        "tau-screen-background": theme.screen_background,
        "tau-screen-text": theme.screen_text,
        "tau-chrome-background": theme.chrome_background,
        "tau-chrome-text": theme.chrome_text,
        "tau-muted-text": theme.muted_text,
        "tau-sidebar-background": theme.sidebar_background,
        "tau-border": theme.border,
        "tau-transcript-background": theme.transcript_background,
        "tau-prompt-background": theme.prompt_background,
        "tau-prompt-text": theme.prompt_text,
        "tau-prompt-border": theme.prompt_border,
        "tau-autocomplete-background": theme.autocomplete_background,
        "tau-accent": theme.accent,
        "tau-highlight-background": theme.highlight_background,
        "tau-highlight-text": theme.highlight_text,
        "tau-markdown-highlight": theme.markdown_heading,
        "tau-markdown-table-header": theme.markdown_table_header,
        "tau-markdown-table-border": theme.markdown_table_border,
        "tau-markdown-inline-code": theme.markdown_inline_code,
        "tau-markdown-code-block-background": theme.markdown_code_block_background,
        "tau-markdown-link": theme.markdown_link,
        "tau-markdown-bullet": theme.markdown_bullet,
        "footer-background": theme.chrome_background,
        "footer-foreground": theme.chrome_text,
        "footer-description-background": theme.chrome_background,
        "footer-description-foreground": theme.chrome_text,
        "footer-key-background": theme.chrome_background,
        "footer-key-foreground": theme.accent,
        "footer-item-background": theme.chrome_background,
    }


def _render_queued_messages(state: TuiState, *, theme: TuiTheme) -> Group:
    """Render queued prompts stacked above the prompt input."""
    rows: list[Text] = []
    for message in state.queued_steering:
        row = Text("↪ steering · queued: ", style=theme.muted_text)
        row.append(_queued_message_preview(message), style=theme.prompt_text)
        rows.append(row)
    for message in state.queued_follow_up:
        row = Text("↳ follow-up · queued: ", style=theme.muted_text)
        row.append(_queued_message_preview(message), style=theme.prompt_text)
        rows.append(row)
    return Group(*rows)


def _queued_message_preview(message: str) -> str:
    """Return the single-line preview shown above the prompt."""
    lines = message.splitlines()
    return lines[0] if lines else ""


def _prompt_footer_mode(
    state: TuiState,
    completion_state: CompletionState,
) -> Literal["normal", "completion", "running"]:
    if completion_state.items:
        return "completion"
    if state.running:
        return "running"
    return "normal"


def _key_hint(key: str) -> str:
    return "+".join(part.capitalize() for part in key.split("+"))


def _app_bindings(keybindings: TuiKeybindings) -> list[Binding]:
    return [
        Binding(keybindings.cancel, "cancel", "Cancel"),
        Binding(keybindings.command_palette, "open_command_palette", "Commands"),
        Binding(keybindings.session_picker, "open_session_picker", "Sessions"),
        Binding(keybindings.thinking_cycle, "cycle_thinking", "Thinking"),
        Binding(keybindings.model_cycle, "cycle_model", "Model"),
        Binding(
            keybindings.accept_completion,
            "accept_completion",
            "Complete",
            priority=True,
        ),
        Binding(
            keybindings.queue_follow_up,
            "submit_follow_up",
            "Follow-up",
            priority=True,
        ),
        Binding(
            keybindings.completion_next,
            "completion_next",
            "Next completion",
            priority=True,
        ),
        Binding(
            keybindings.completion_previous,
            "completion_previous",
            "Previous completion",
            priority=True,
        ),
        Binding(keybindings.toggle_tool_results, "toggle_tool_results", "Tool results"),
        Binding(keybindings.toggle_thinking, "toggle_thinking", "Thinking tokens"),
        Binding(keybindings.copy_message, "clear_prompt", "Clear input"),
        Binding(keybindings.quit, "quit", "Quit"),
    ]


def _prompt_bindings(
    keybindings: TuiKeybindings,
    *,
    mode: Literal["normal", "completion", "running"],
) -> list[Binding]:
    if mode == "completion":
        bindings = [
            Binding(
                keybindings.accept_completion,
                "accept_completion",
                "Complete",
                key_display=f"{_key_hint(keybindings.accept_completion)}/Enter",
                priority=True,
            ),
            Binding(
                keybindings.completion_next,
                "completion_next",
                "Choose",
                key_display=(
                    f"{_key_hint(keybindings.completion_previous)}/"
                    f"{_key_hint(keybindings.completion_next)}"
                ),
                priority=True,
            ),
            Binding(keybindings.cancel, "cancel", "Close", priority=True),
        ]
        return bindings + _hidden_prompt_bindings(keybindings, visible_bindings=bindings)
    if mode == "running":
        bindings = [
            Binding("enter", "submit_prompt", "Steer", priority=True),
            Binding(keybindings.queue_follow_up, "submit_follow_up", "Follow-up", priority=True),
            Binding(keybindings.cancel, "cancel", "Cancel", priority=True),
            Binding(
                keybindings.toggle_thinking,
                "toggle_thinking",
                "Thinking",
                priority=True,
            ),
            Binding(
                keybindings.toggle_tool_results,
                "toggle_tool_results",
                "Tools",
                priority=True,
            ),
        ]
        return bindings + _hidden_prompt_bindings(keybindings, visible_bindings=bindings)
    bindings = [
        Binding("enter", "submit_prompt", "Submit", priority=True),
        Binding("shift+enter", "insert_newline", "Newline", priority=True),
        Binding(keybindings.command_palette, "open_command_palette", "Commands", priority=True),
        Binding(keybindings.session_picker, "open_session_picker", "Sessions", priority=True),
        Binding(keybindings.thinking_cycle, "cycle_thinking", "Thinking", priority=True),
        Binding(keybindings.model_cycle, "cycle_model", "Model", priority=True),
        Binding(
            keybindings.copy_message,
            "clear_prompt",
            "Clear",
            priority=True,
        ),
        Binding(keybindings.quit, "quit", "Quit", priority=True),
    ]
    return bindings + _hidden_prompt_bindings(keybindings, visible_bindings=bindings)


def _hidden_prompt_bindings(
    keybindings: TuiKeybindings,
    *,
    visible_bindings: Sequence[Binding],
) -> list[Binding]:
    visible_keys = {key for binding in visible_bindings for key in binding.key.split(",")}
    candidates = (
        (keybindings.command_palette, "open_command_palette"),
        (keybindings.session_picker, "open_session_picker"),
        (keybindings.queue_follow_up, "submit_follow_up"),
        (keybindings.thinking_cycle, "cycle_thinking"),
        (keybindings.model_cycle, "cycle_model"),
        (keybindings.toggle_tool_results, "toggle_tool_results"),
        (keybindings.toggle_thinking, "toggle_thinking"),
        (keybindings.copy_message, "clear_prompt"),
        (keybindings.accept_completion, "accept_completion"),
        (keybindings.completion_next, "completion_next"),
        (keybindings.completion_previous, "completion_previous"),
        (keybindings.quit, "quit"),
    )
    return [
        Binding(key, action, show=False, priority=True)
        for key, action in candidates
        if key not in visible_keys
    ]


def _text_end_location(text: str) -> tuple[int, int]:
    """Return the TextArea cursor location at the end of text."""
    line, _, column_text = text.rpartition("\n")
    return (line.count("\n") + 1 if line else 0, len(column_text))


def _format_prompt_error(exc: BaseException, session: CodingSession) -> str:
    detail = str(exc) or type(exc).__name__
    message = f"Error: {detail}"
    log_path = getattr(session, "last_diagnostic_log_path", None)
    if isinstance(log_path, Path):
        return f"{message}\nLog: {log_path}"
    return message


def _attach_diagnostic_log_path_to_error(state: TuiState, session: CodingSession) -> None:
    log_path = getattr(session, "last_diagnostic_log_path", None)
    if not isinstance(log_path, Path) or state.error is None:
        return
    message = f"Error: {state.error}\nLog: {log_path}"
    state.error = message
    for item in reversed(state.items):
        if item.role == "error":
            item.text = message
            return
    state.add_item("error", message)


def _explicit_resume_record(
    manager: SessionManager,
    *,
    session_id: str | None,
) -> CodingSessionRecord | None:
    if session_id is None:
        return None
    record = manager.get_session(session_id)
    if record is None:
        raise RuntimeError(f"Unknown session: {session_id}")
    return record


def _create_startup_session_record(
    manager: SessionManager,
    *,
    cwd: Path,
    selection: ProviderSelection,
) -> CodingSessionRecord:
    try:
        return manager.prepare_session(
            cwd=cwd,
            model=selection.model,
            provider_name=selection.provider.name,
        )
    except TypeError:
        return manager.prepare_session(cwd=cwd, model=selection.model)


def _resolve_tui_startup_selection(
    settings: Any,
    *,
    record: Any | None,
    provider_name: str | None,
    model: str | None,
    explicit_resume: bool,
) -> ProviderSelection:
    if provider_name is not None or model is not None:
        return resolve_provider_selection(settings, provider_name=provider_name, model=model)

    if explicit_resume:
        record_selection = _selection_from_session_record(settings, record)
        if record_selection is not None:
            return record_selection

    default_selection = resolve_provider_selection(settings)
    if provider_has_usable_credentials(
        default_selection.provider,
        credential_reader=FileCredentialStore(),
    ):
        return default_selection

    fallback_selection = _first_usable_startup_selection(settings)
    return fallback_selection or default_selection


def _first_usable_startup_selection(settings: Any) -> ProviderSelection | None:
    credential_store = FileCredentialStore()
    for provider in settings.providers:
        if provider_has_usable_credentials(provider, credential_reader=credential_store):
            return ProviderSelection(provider=provider, model=provider.default_model)
    return None


def _selection_from_session_record(settings: Any, record: Any | None) -> ProviderSelection | None:
    if record is None:
        return None
    record_model = getattr(record, "model", None)
    if not isinstance(record_model, str) or not record_model:
        return None

    record_provider = getattr(record, "provider_name", None)
    if isinstance(record_provider, str) and record_provider:
        try:
            return resolve_provider_selection(
                settings,
                provider_name=record_provider,
                model=record_model,
            )
        except Exception:
            return None

    for choice in _usable_scoped_startup_choices(settings):
        if choice.model == record_model:
            return resolve_provider_selection(
                settings,
                provider_name=choice.provider_name,
                model=choice.model,
            )

    credential_store = FileCredentialStore()
    for provider in settings.providers:
        if record_model not in provider.models:
            continue
        if not provider_has_usable_credentials(provider, credential_reader=credential_store):
            continue
        return ProviderSelection(provider=provider, model=record_model)
    return None


def _usable_scoped_startup_choices(settings: Any) -> tuple[ModelChoice, ...]:
    credential_store = FileCredentialStore()
    choices: list[ModelChoice] = []
    for item in settings.scoped_models:
        try:
            provider = settings.get_provider(item.provider)
        except Exception:
            continue
        if item.model not in provider.models:
            continue
        if not provider_has_usable_credentials(provider, credential_reader=credential_store):
            continue
        choices.append(ModelChoice(provider_name=item.provider, model=item.model))
    return tuple(choices)


async def run_tui_app(
    *,
    model: str | None,
    cwd: Path,
    session_id: str | None = None,
    new_session: bool = False,
    provider_name: str | None = None,
    auto_compact_token_threshold: int | None = None,
    initial_prompt: str | None = None,
    session_manager: SessionManager | None = None,
    startup_notice: str | None = None,
    startup_notices: Sequence[str] = (),
    extension_paths: tuple[Path, ...] = (),
    extensions_enabled: bool = True,
    project_extensions_enabled: bool = False,
) -> None:
    """Create the default provider/session and run the Textual app."""
    if new_session and session_id is not None:
        raise RuntimeError("--resume and --new-session cannot be used together")

    provider_settings = load_provider_settings()
    shell_settings = load_shell_settings()
    manager = session_manager or SessionManager()
    record = _explicit_resume_record(
        manager,
        session_id=session_id,
    )
    selection = _resolve_tui_startup_selection(
        provider_settings,
        record=record,
        provider_name=provider_name,
        model=model,
        explicit_resume=session_id is not None,
    )
    startup_message: str | None = None
    startup_error_notice: str | None = None
    runtime_provider_config: ProviderConfig | None = selection.provider
    try:
        provider = create_model_provider(
            selection.provider,
            model=selection.model,
            thinking_level=resolve_startup_thinking_level(
                selection.provider,
                selection.model,
            ),
        )
    except RuntimeError as exc:
        # Most startup RuntimeErrors are missing credentials, but surface the real
        # cause so a non-auth failure is not silently misreported as "Login required".
        login_required_message = (
            "Login required. Run /login to choose a provider, "
            f"or /login {selection.provider.name} to continue with the current provider."
        )
        startup_message = f"{login_required_message}\n\nStartup error: {exc}"
        startup_error_notice = (
            f"Startup provider creation failed for "
            f"{selection.provider.name}:{selection.model}: {exc}"
        )
        provider = LoginRequiredProvider(startup_message)
        runtime_provider_config = None
    session: CodingSession | None = None
    try:
        index_on_first_persist = False
        if record is None:
            record = _create_startup_session_record(
                manager,
                cwd=cwd,
                selection=selection,
            )
            index_on_first_persist = manager.get_session(record.id) is None

        session = await CodingSession.load(
            CodingSessionConfig(
                provider=provider,
                model=record.model or selection.model,
                cwd=record.cwd,
                storage=jsonl_session_storage(record.path),
                session_id=record.id,
                session_manager=manager,
                provider_name=selection.provider.name,
                provider_settings=provider_settings,
                runtime_provider_config=runtime_provider_config,
                auto_compact_token_threshold=auto_compact_token_threshold,
                index_on_first_persist=index_on_first_persist,
                shell_command_prefix=shell_settings.shell_command_prefix,
                extension_paths=extension_paths,
                extensions_enabled=extensions_enabled,
                project_extensions_enabled=project_extensions_enabled,
            )
        )
        custom_themes, theme_diagnostics = load_custom_tui_themes(
            TauResourcePaths(cwd=record.cwd).themes_dirs
        )
        set_custom_tui_themes(custom_themes)
        legacy_notices = (startup_notice,) if startup_notice else ()
        error_notices = (startup_error_notice,) if startup_error_notice else ()
        theme_notices = tuple(diagnostic.format() for diagnostic in theme_diagnostics)
        all_startup_notices = tuple(
            (*error_notices, *startup_notices, *legacy_notices, *theme_notices)
        )
        app = TauTuiApp(
            session,
            tui_settings=load_tui_settings(),
            startup_message=startup_message,
            startup_notices=all_startup_notices,
            initial_prompt=initial_prompt,
        )
        await app.run_async()
    finally:
        if session is not None:
            close_session = getattr(session, "aclose", None)
            if close_session is not None:
                await close_session()
        await provider.aclose()
