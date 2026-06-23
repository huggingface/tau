"""Minimal Textual app for Tau coding sessions."""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from inspect import isawaitable
from pathlib import Path
from typing import Any, ClassVar, Literal, Protocol, cast

from rich.console import Group
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingsMap
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.events import Key, Resize
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TextArea,
)
from textual.worker import Worker

from tau_agent import (
    AgentEndEvent,
    AgentEvent,
    AgentStartEvent,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    QueueUpdateEvent,
    RetryEvent,
    ThinkingDeltaEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from tau_agent.messages import AgentMessage
from tau_agent.tools import AgentTool
from tau_ai import ProviderErrorEvent, ProviderEvent
from tau_ai.provider import CancellationToken
from tau_coding.commands import CommandRegistry, create_default_command_registry
from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.oauth import OAuthAuthInfo, OAuthPrompt, login_openai_codex
from tau_coding.provider_catalog import (
    BUILTIN_PROVIDER_CATALOG,
    ProviderCatalogEntry,
    builtin_provider_entry,
)
from tau_coding.provider_config import (
    ProviderConfig,
    ProviderSelection,
    load_provider_settings,
    provider_config_from_catalog_entry,
    provider_has_usable_credentials,
    resolve_provider_selection,
    upsert_saved_provider,
)
from tau_coding.provider_runtime import create_model_provider
from tau_coding.session import (
    CodingSession,
    CodingSessionConfig,
    ModelChoice,
    SessionTreeChoice,
    jsonl_session_storage,
    parse_terminal_command,
)
from tau_coding.session_manager import CodingSessionRecord, SessionManager
from tau_coding.thinking import DEFAULT_THINKING_LEVEL
from tau_coding.tui.adapter import TuiEventAdapter
from tau_coding.tui.autocomplete import CompletionOption, CompletionState, build_completion_state
from tau_coding.tui.config import (
    BUILTIN_TUI_THEME_NAMES,
    TuiKeybindings,
    TuiSettings,
    TuiTheme,
    TuiThemeName,
    load_tui_settings,
    save_tui_settings,
)
from tau_coding.tui.state import TuiState, format_terminal_command_result_block
from tau_coding.tui.widgets import (
    CompactSessionInfo,
    SessionSidebar,
    TranscriptView,
    render_completion_suggestions,
)

type BindingEntry = Binding | tuple[str, str] | tuple[str, str, str]
SIDEBAR_MIN_WIDTH = 96
SIDEBAR_MIN_HEIGHT = 24
ACTIVITY_TICK_SECONDS = 0.15
ACTIVITY_COLOR_FADE_STEPS = 24
ACTIVITY_INDICATOR_HEIGHT = 3


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
    ) -> AsyncIterator[ProviderEvent]:
        """Surface a login-needed provider error."""
        del model, system, messages, tools, signal

        async def iterator() -> AsyncIterator[ProviderEvent]:
            yield ProviderErrorEvent(message=self.message)

        return iterator()


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

    def action_edit_queued_follow_up(self) -> bool: ...

    async def action_submit_prompt(self) -> None: ...

    async def action_submit_follow_up(self) -> None: ...


class SessionCompletionRecord(Protocol):
    """Session metadata needed to render resume picker completions."""

    id: str
    title: str | None
    model: str
    cwd: Path
    updated_at: float


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
        super().__init__(**kwargs)
        self.tui_keybindings = tui_keybindings or TuiKeybindings()
        self._base_bindings = self._bindings.copy()
        self._footer_mode: Literal["normal", "completion", "running"] = "normal"
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
        elif self._completion_target().action_edit_queued_follow_up():
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

    async def on_key(self, event: Key) -> None:
        """Route completion and submission keys before default input handling."""
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


class SessionPickerScreen(ModalScreen[str | None]):
    """Minimal modal picker for indexed sessions."""

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
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the session picker."""
        with Vertical(id="session-picker"):
            yield Static("Sessions", id="session-picker-title")
            yield ListView(
                *[
                    ListItem(Label(_session_picker_label(record), markup=False))
                    for record in self.records
                ],
                id="session-picker-list",
            )
            yield Static("Enter selects - Escape closes", id="session-picker-help")

    def on_mount(self) -> None:
        """Focus the session list for keyboard navigation."""
        session_list = self.query_one("#session-picker-list", ListView)
        session_list.index = 0
        session_list.focus()

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
        self.dismiss(self.records[event.index].id)

    def action_cursor_up(self) -> None:
        """Move to the previous session."""
        self.query_one("#session-picker-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next session."""
        self.query_one("#session-picker-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Select the highlighted session."""
        self.query_one("#session-picker-list", ListView).action_select_cursor()

    def action_cancel(self) -> None:
        """Close the picker without selecting a session."""
        self.dismiss(None)


@dataclass(frozen=True, slots=True)
class TreePickerResult:
    """Tree-picker branch selection."""

    entry_id: str
    summarize: bool = False
    custom_instructions: str | None = None


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
        return [
            ListItem(Label(_tree_picker_label(choice, theme=self.theme), markup=False))
            for choice in self._visible_choices()
        ]

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

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
        Binding("up", "scroll_up", "Scroll up", show=False, priority=True),
        Binding("down", "scroll_down", "Scroll down", show=False, priority=True),
    ]

    def __init__(self, title: str, message: str, *, theme: TuiTheme) -> None:
        super().__init__()
        self.title_text = title
        self.message = message
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose command output."""
        with Vertical(id="command-output"):
            yield Static(self.title_text, id="command-output-title")
            with CommandOutputScroll(id="command-output-scroll"):
                yield Static(self.message, id="command-output-body", markup=False)
            yield Static("Enter or Escape closes", id="command-output-help")

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

    def action_scroll_up(self) -> None:
        """Scroll command output up."""
        self.query_one("#command-output-scroll", CommandOutputScroll).action_scroll_up()

    def action_scroll_down(self) -> None:
        """Scroll command output down."""
        self.query_one("#command-output-scroll", CommandOutputScroll).action_scroll_down()


class LoginProviderPickerScreen(ModalScreen[str | None]):
    """Provider picker for the TUI login flow."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select_cursor", "Select", show=False),
    ]

    def __init__(
        self,
        providers: Sequence[ProviderCatalogEntry],
        *,
        theme: TuiTheme,
    ) -> None:
        super().__init__()
        self.providers = tuple(providers)
        self.theme = theme

    def compose(self) -> ComposeResult:
        """Compose the provider picker."""
        with Vertical(id="login-provider-picker"):
            yield Static("Login", id="login-provider-title")
            yield ListView(
                *[
                    ListItem(Label(_login_provider_label(provider), markup=False))
                    for provider in self.providers
                ],
                id="login-provider-list",
            )
            yield Static("Enter selects - Escape closes", id="login-provider-help")

    def on_mount(self) -> None:
        """Focus the provider list."""
        provider_list = self.query_one("#login-provider-list", ListView)
        provider_list.index = 0
        provider_list.focus()

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
        self.dismiss(self.providers[event.index].name)

    def action_cursor_up(self) -> None:
        """Move to the previous provider."""
        self.query_one("#login-provider-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        """Move to the next provider."""
        self.query_one("#login-provider-list", ListView).action_cursor_down()

    def action_select_cursor(self) -> None:
        """Select the highlighted provider."""
        self.query_one("#login-provider-list", ListView).action_select_cursor()

    def action_cancel(self) -> None:
        """Close without selecting a provider."""
        self.dismiss(None)


class LoginMethodPickerScreen(ModalScreen[str | None]):
    """Login method picker for the TUI login flow."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
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
                    Label("Subscription\n  Sign in with an OAuth account.", markup=False),
                    id="login-method-subscription",
                ),
                ListItem(
                    Label("API key\n  Save a provider API key.", markup=False),
                    id="login-method-api-key",
                ),
                id="login-method-list",
            )
            yield Static("Enter selects - Escape closes", id="login-method-help")

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

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Dismiss with the selected login method."""
        if event.item.id == "login-method-subscription":
            self.dismiss("subscription")
        elif event.item.id == "login-method-api-key":
            self.dismiss("api-key")

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
    """Theme picker for the built-in TUI themes."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select_cursor", "Select", show=False, priority=True),
    ]

    def __init__(self, *, current_theme: TuiThemeName, theme: TuiTheme) -> None:
        super().__init__()
        self.current_theme = current_theme
        self.theme = theme

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
                    for theme_name in BUILTIN_TUI_THEME_NAMES
                ],
                id="theme-picker-list",
            )
            yield Static("Enter selects - Escape closes", id="theme-picker-help")

    def on_mount(self) -> None:
        """Select the current theme."""
        theme_list = self.query_one("#theme-picker-list", ListView)
        try:
            theme_list.index = BUILTIN_TUI_THEME_NAMES.index(self.current_theme)
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
        self.dismiss(BUILTIN_TUI_THEME_NAMES[event.index])

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


class LoginScreen(ModalScreen[str | None]):
    """Password prompt for saving a provider API key."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
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
            yield Static("Enter saves - Escape closes", id="login-footer")

    def on_mount(self) -> None:
        """Focus the API key field."""
        self.query_one("#login-api-key", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Dismiss with the submitted API key."""
        if event.input.id != "login-api-key":
            return
        event.stop()
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        """Close without saving."""
        self.dismiss(None)


class OAuthLoginScreen(ModalScreen[OAuthCredential | None]):
    """OAuth login flow for providers backed by subscription auth."""

    BINDINGS: ClassVar[list[BindingEntry]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, provider: ProviderCatalogEntry, *, theme: TuiTheme) -> None:
        super().__init__()
        self.provider = provider
        self.theme = theme
        self._manual_code_future: asyncio.Future[str] | None = None
        self._manual_code_value: str | None = None

    def compose(self) -> ComposeResult:
        """Compose the OAuth login prompt."""
        with Vertical(id="login-screen"):
            yield Static(f"Login: {self.provider.display_name}", id="login-title")
            yield Static("Complete the browser login, or paste the redirect URL.", id="login-help")
            yield Static("", id="login-oauth-url")
            yield Input(
                placeholder="Paste redirect URL or authorization code",
                id="login-oauth-code",
            )
            yield Static("Enter submits - Escape closes", id="login-footer")

    def on_mount(self) -> None:
        """Focus the manual-code field and start OAuth."""
        self.query_one("#login-oauth-code", Input).focus()
        self.run_worker(self._run_login(), exclusive=True)

    async def _run_login(self) -> None:
        try:
            credential = await login_openai_codex(
                on_auth=self._show_auth,
                on_prompt=self._prompt_for_code,
                on_manual_code_input=self._manual_code_input,
            )
        except Exception as exc:  # noqa: BLE001 - surface OAuth failures in the TUI
            self.query_one("#login-help", Static).update(f"OAuth failed: {exc}")
            return
        self.dismiss(credential)

    def _show_auth(self, info: OAuthAuthInfo) -> None:
        self.query_one("#login-oauth-url", Static).update(info.url)
        if info.instructions:
            self.query_one("#login-help", Static).update(info.instructions)

    async def _prompt_for_code(self, prompt: OAuthPrompt) -> str:
        self.query_one("#login-help", Static).update(prompt.message)
        return await self._manual_code_input()

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
        if not value:
            return
        self._manual_code_value = value
        if self._manual_code_future is not None and not self._manual_code_future.done():
            self._manual_code_future.set_result(value)

    def action_cancel(self) -> None:
        """Close without saving OAuth credentials."""
        if self._manual_code_future is not None and not self._manual_code_future.done():
            self._manual_code_future.cancel()
        self.dismiss(None)


class TauTuiApp(App[None]):
    """Interactive Textual frontend for a ``CodingSession``."""

    TITLE = "Tau"
    CSS = """
    Screen {
        layout: vertical;
        background: $tau-screen-background;
        color: $tau-screen-text;
    }

    Header {
        background: $tau-chrome-background;
        color: $tau-muted-text;
        dock: top;
    }

    Footer {
        background: $tau-chrome-background;
        color: $tau-chrome-text;
    }

    Footer FooterKey {
        background: $tau-chrome-background;
        color: $tau-chrome-text;
    }

    Footer FooterKey .footer-key--key {
        background: $tau-chrome-background;
        color: $tau-accent;
    }

    Footer FooterKey .footer-key--description,
    Footer FooterLabel {
        background: $tau-chrome-background;
        color: $tau-chrome-text;
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
        width: 32;
        min-width: 28;
        height: 1fr;
        padding: 1 1 0 0;
        background: $tau-sidebar-background;
        border-right: tall $tau-border;
    }

    TauTuiApp.-hide-sidebar #sidebar {
        display: none;
    }

    TauTuiApp.-hide-sidebar #main-pane {
        padding-left: 1;
    }

    #main-pane {
        width: 1fr;
        padding: 1 1 0 1;
    }

    #transcript {
        height: 1fr;
        border: none;
        background: $tau-transcript-background;
        padding: 0 1 0 0;
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

    #prompt {
        width: 1fr;
        height: auto;
        background: $tau-prompt-background;
        color: $tau-prompt-text;
        border: tall transparent;
        margin: 0;
        padding: 0 1;
        max-height: 8;
    }

    #activity-indicator {
        width: 1;
        height: 3;
        margin: 0 0 0 1;
        background: transparent;
        color: transparent;
    }

    #prompt:focus {
        border: tall $tau-prompt-border;
    }

    #prompt.-shell-mode {
        border: tall $tau-accent;
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

    #session-picker-list,
    #tree-picker-list {
        height: auto;
        max-height: 16;
        background: $tau-transcript-background;
        border: tall $tau-border;
    }

    ListView > ListItem.--highlight {
        background: $tau-highlight-background;
        color: $tau-highlight-text;
    }

    ListView > ListItem.--highlight Label {
        background: $tau-highlight-background;
        color: $tau-highlight-text;
    }

    #session-picker-help,
    #tree-picker-help {
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

    #login-method-intro {
        height: 1;
        color: $tau-muted-text;
        margin-bottom: 1;
    }

    #login-method-list {
        max-height: 6;
    }

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

    #login-help {
        height: 1;
        color: $tau-muted-text;
        margin-bottom: 1;
    }

    #login-api-key,
    #login-oauth-code {
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
        initial_prompt: str | None = None,
    ) -> None:
        self.tui_settings = tui_settings or TuiSettings()
        self.startup_message = startup_message
        self.initial_prompt = initial_prompt
        super().__init__()
        self._bindings = BindingsMap(_app_bindings(self.tui_settings.keybindings))
        self.session = session
        self.state = TuiState(skills=session.skills)
        self.state.load_messages(session.messages)
        self.adapter = TuiEventAdapter(self.state)
        self._prompt_worker: Worker[None] | None = None
        self._prompt_run_id = 0
        self._completion_state = CompletionState()
        self._activity_frame = 0
        self._activity_timer: Timer | None = None
        self._active_notification_keys: set[tuple[str, str]] = set()
        self._supports_pyperclip: bool | None = None

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

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Return Tau-specific CSS variables for the selected TUI theme."""
        variables = super().get_theme_variable_defaults()
        return {**variables, **_theme_css_variables(self.tui_settings.resolved_theme)}

    def compose(self) -> ComposeResult:
        """Compose the TUI widgets."""
        yield Header()
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
                yield Static("", id="queued-messages")
                with Horizontal(id="prompt-row"):
                    yield PromptInput(
                        placeholder="Ask Tau…  Enter submits, Shift+Enter inserts a newline",
                        id="prompt",
                        tui_keybindings=self.tui_settings.keybindings,
                    )
                    yield Static("", id="activity-indicator")
                yield CompactSessionInfo(id="compact-session-info")
                yield Static("", id="autocomplete")
        yield Footer()

    async def on_mount(self) -> None:
        """Focus the prompt when the app starts."""
        prompt = self.query_one(PromptInput)
        prompt.shell_mode_style = self.tui_settings.resolved_theme.accent
        self._sync_prompt_shell_mode(prompt.text)
        prompt.focus()
        self._update_responsive_layout(self.size.width, self.size.height)
        self._refresh()
        self._refresh_completions()
        if self.startup_message:
            self._notify(self.startup_message, severity="warning")
        if self.initial_prompt and self.initial_prompt.strip():
            self._submit_prompt(self.initial_prompt.strip())

    def on_unmount(self) -> None:
        """Stop the activity timer when the app is torn down."""
        if self._activity_timer is not None:
            self._activity_timer.stop()
            self._activity_timer = None

    def on_resize(self, event: Resize) -> None:
        """Update responsive chrome when the terminal changes size."""
        self._update_responsive_layout(event.size.width, event.size.height)

    @on(events.TextSelected)
    async def on_text_selected(self) -> None:
        """Optionally copy selected transcript text automatically."""
        if not self.tui_settings.auto_copy_selection:
            return
        selection = self.screen.get_selected_text()
        if selection:
            self.copy_to_clipboard(selection)
            self._notify("Copied selection to clipboard.")

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Update prompt autocomplete when the prompt text changes."""
        if event.text_area.id != "prompt":
            return
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
        raw_text = prompt.text
        applied_completion = self._apply_selected_completion(raw_text)
        if applied_completion is not None and applied_completion != raw_text:
            prompt.text = applied_completion
            prompt.move_cursor(_text_end_location(applied_completion))
            self._completion_state = self._build_completion_state(applied_completion)
            self._refresh_completions()
            return

        text = raw_text.strip()
        prompt.text = ""
        self._completion_state = CompletionState()
        self._refresh_completions()
        if not text:
            return

        terminal_command = parse_terminal_command(text)
        if terminal_command is not None:
            await self._run_terminal_command(
                terminal_command.command,
                add_to_context=terminal_command.add_to_context,
            )
            return

        command = self.session.handle_command(text)
        if command.handled:
            if command.clear_requested:
                self.state.clear()
            if command.new_session_requested:
                await self._new_session()
            if command.compact_summary is not None:
                try:
                    self.state.clear()
                    self.state.add_item("status", "Compacting session…")
                    self._refresh()
                    compact_message = await self.session.compact(command.compact_summary)
                    self.state.clear()
                    self.state.set_skills(self.session.skills)
                    self.state.load_messages(self.session.messages)
                    self._notify(compact_message)
                except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
                    self._notify(f"Error: {exc}", severity="error")
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
                await self._open_tree_picker()
            if command.login_picker_requested:
                self._open_login_picker()
            if command.login_provider is not None:
                self._open_login(command.login_provider)
            if command.model_picker_requested:
                self._open_model_picker()
            if command.scoped_models_picker_requested:
                self._open_scoped_models_picker()
            if command.theme_picker_requested:
                self._open_theme_picker()
            if command.thinking_level is not None:
                await self._set_thinking_level(command.thinking_level)
            if command.theme is not None:
                self._set_tui_theme(cast(TuiThemeName, command.theme))
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
            await self._queue_prompt(text, streaming_behavior=streaming_behavior)
            return

        self._submit_prompt(text)

    def _submit_prompt(self, text: str) -> None:
        """Add a prompt to the transcript and start the agent worker."""
        self._prompt_run_id += 1
        run_id = self._prompt_run_id
        self._refresh()
        self._prompt_worker = self.run_worker(self._run_prompt(text, run_id), exclusive=True)

    async def _run_terminal_command(self, command: str, *, add_to_context: bool) -> None:
        run_terminal_command = getattr(self.session, "run_terminal_command", None)
        if not callable(run_terminal_command):
            self._notify("Terminal commands are not available.", severity="error")
            return
        try:
            result = await run_terminal_command(command, add_to_context=add_to_context)
        except Exception as exc:  # noqa: BLE001 - surface command execution failures in the TUI
            self._notify(f"Could not run command: {exc}", severity="error")
            return
        self.state.add_item(
            "tool",
            f"$ {result.command}",
            tool_result_text=format_terminal_command_result_block(
                ok=result.ok,
                added_to_context=result.added_to_context,
                output=result.output,
            ),
            always_show_tool_result=True,
        )
        self._refresh()

    def _set_tui_theme(self, theme: TuiThemeName) -> None:
        self.tui_settings = TuiSettings(
            keybindings=self.tui_settings.keybindings,
            theme=theme,
            auto_copy_selection=self.tui_settings.auto_copy_selection,
        )
        save_tui_settings(self.tui_settings)
        self.refresh_css(animate=False)
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
        self._refresh()

    async def _run_prompt(self, text: str, run_id: int | None = None) -> None:
        """Run one prompt and stream session events into the TUI state."""
        active_run_id = self._prompt_run_id if run_id is None else run_id
        try:
            async for event in self.session.prompt(text):
                if active_run_id != self._prompt_run_id:
                    return
                self.adapter.apply(event)
                if isinstance(event, ErrorEvent) and not event.recoverable:
                    _attach_diagnostic_log_path_to_error(self.state, self.session)
                await self._apply_streaming_transcript_event(event)
        except Exception as exc:  # noqa: BLE001 - surface unexpected worker errors in the TUI
            if active_run_id != self._prompt_run_id:
                return
            message = _format_prompt_error(exc, self.session)
            self.state.error = message
            self.state.add_item("error", message)
            self.state.running = False
            self._refresh()
        finally:
            if active_run_id == self._prompt_run_id:
                self._prompt_worker = None

    async def _apply_streaming_transcript_event(self, event: AgentEvent) -> None:
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
        if isinstance(event, MessageDeltaEvent):
            await transcript.append_assistant_delta(event.delta, theme=theme)
            self._sync_activity_indicator()
            return
        if isinstance(event, ThinkingDeltaEvent):
            await transcript.append_thinking_delta(
                event.delta,
                theme=theme,
                show_thinking=self.state.show_thinking,
            )
            self._sync_activity_indicator()
            return
        if isinstance(event, MessageEndEvent):
            if event.message.role == "user":
                self._refresh()
                return
            if event.message.role == "assistant":
                await transcript.finish_assistant_message(event.message.content)
                self._refresh_chrome()
                return
            return
        if isinstance(event, ToolExecutionStartEvent):
            await transcript.finish_assistant_message()
            await transcript.append_item(
                self.state.items[-1],
                theme=theme,
                show_tool_results=self.state.show_tool_results,
            )
            self._refresh_chrome()
            return
        if isinstance(event, ToolExecutionUpdateEvent | RetryEvent | ErrorEvent):
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
            self._refresh()
            return
        if isinstance(event, QueueUpdateEvent):
            self._refresh_chrome()
            return
        self._refresh_chrome()

    def action_cancel(self) -> None:
        """Cancel the active agent turn."""
        self._cancel_active_prompt(notify=True)

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
            | ThemePickerScreen,
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
            | ModelPickerScreen,
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
            | ModelPickerScreen,
        ):
            self.screen.action_cursor_up()
            return
        if not self._completion_state.items:
            if self.action_edit_queued_follow_up():
                return
            self.query_one("#prompt", PromptInput).action_cursor_up()
            return
        self._completion_state = self._completion_state.select_previous()
        self._refresh_completions()

    def action_edit_queued_follow_up(self) -> bool:
        """Move the latest queued follow-up back into the prompt for editing."""
        if not self.state.running:
            return False
        prompt = self.query_one("#prompt", PromptInput)
        if prompt.text.strip():
            return False
        pop_follow_up = getattr(self.session, "pop_latest_follow_up_message", None)
        if not callable(pop_follow_up):
            return False
        message = pop_follow_up()
        if not message:
            return False
        prompt.text = message
        prompt.move_cursor(_text_end_location(message))
        self._sync_queue_state()
        self._completion_state = self._build_completion_state(prompt.text)
        self._refresh()
        return True

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
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        self.run_worker(self._cycle_thinking_level(), exclusive=False)

    def action_cycle_model(self) -> None:
        """Cycle through scoped models."""
        if self.state.running:
            self._notify("Tau is already working. Press Escape to cancel.")
            return
        self.run_worker(self._cycle_scoped_model(), exclusive=False)

    def action_toggle_tool_results(self) -> None:
        """Toggle inline tool result details in the transcript."""
        expanded = self.state.toggle_tool_results()
        self._refresh()
        self._notify("Tool results expanded." if expanded else "Tool results collapsed.")

    def action_toggle_thinking(self) -> None:
        """Toggle thinking-token display in the transcript."""
        self.state.toggle_thinking()
        self._refresh()

    def _handle_session_picker_result(self, session_id: str | None) -> None:
        if session_id is None:
            return
        self.run_worker(self._resume_session(session_id), exclusive=False)

    async def _resume_session(self, session_id: str) -> None:
        try:
            resume_message = await self.session.resume(session_id)
            self.state.clear()
            self.state.set_skills(self.session.skills)
            self.state.load_messages(self.session.messages)
            self._notify(resume_message)
        except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
            self._notify(f"Error: {exc}", severity="error")
        self._refresh()

    async def _open_tree_picker(self) -> None:
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
            self.state.load_messages(self.session.messages)
            if isinstance(result, str):
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
            self.state.load_messages(self.session.messages)
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
            ),
            callback=self._handle_login_provider_result,
        )

    def _handle_login_provider_result(self, provider_name: str | None) -> None:
        if provider_name is None:
            return
        self._open_login(provider_name)

    def _open_login(self, provider_name: str) -> None:
        entry = builtin_provider_entry(provider_name)
        if entry is None:
            self._notify(f"Unknown provider: {provider_name}", severity="error")
            return
        if entry.kind == "openai-codex":
            self.push_screen(
                OAuthLoginScreen(entry, theme=self.tui_settings.resolved_theme),
                callback=lambda credential: self._handle_oauth_login_result(entry, credential),
            )
            return
        self.push_screen(
            LoginScreen(entry, theme=self.tui_settings.resolved_theme),
            callback=lambda api_key: self._handle_login_result(entry, api_key),
        )

    def _handle_login_result(self, entry: ProviderCatalogEntry, api_key: str | None) -> None:
        if api_key is None:
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

    def _handle_oauth_login_result(
        self,
        entry: ProviderCatalogEntry,
        credential: OAuthCredential | None,
    ) -> None:
        if credential is None:
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
        self._refresh()

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
        self._refresh()

    def _open_theme_picker(self) -> None:
        self.push_screen(
            ThemePickerScreen(
                current_theme=self.tui_settings.theme,
                theme=self.tui_settings.resolved_theme,
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
        self._refresh()

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
        self._refresh()

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
        self._refresh()

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
        self._sync_queue_state()
        sidebar = self.query_one("#sidebar", SessionSidebar)
        sidebar.update_from_session(self.session, theme=theme)
        compact_info = self.query_one("#compact-session-info", CompactSessionInfo)
        compact_info.update_from_session(self.session, theme=theme)
        queued_messages = self.query_one("#queued-messages", Static)
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

    def _apply_activity_indicator(self) -> None:
        theme = self.tui_settings.resolved_theme
        try:
            prompt = self.query_one("#prompt", PromptInput)
            indicator = self.query_one("#activity-indicator", Static)
        except NoMatches:
            return
        prompt.styles.border = (
            "tall",
            _activity_prompt_border_color(
                theme,
                frame=self._activity_frame,
                running=self.state.running,
                shell_mode=_is_terminal_command_prompt(prompt.text),
            ),
        )
        indicator.update(
            _render_activity_indicator(
                theme,
                frame=self._activity_frame,
                running=self.state.running,
            )
        )

    def _refresh_completions(self) -> None:
        suggestions = self.query_one("#autocomplete", Static)
        suggestions.display = bool(self._completion_state.items)
        suggestions.update(
            render_completion_suggestions(
                self._completion_state,
                theme=self.tui_settings.resolved_theme,
            )
        )
        self._refresh_footer_bindings()

    def _update_responsive_layout(self, width: int, height: int) -> None:
        show_sidebar = width >= SIDEBAR_MIN_WIDTH and height >= SIDEBAR_MIN_HEIGHT
        self.set_class(not show_sidebar, "-hide-sidebar")

    def _build_completion_state(self, text: str) -> CompletionState:
        registry = _session_command_registry(self.session)
        return build_completion_state(
            text,
            command_registry=registry,
            skills=self.session.skills,
            prompt_templates=self.session.prompt_templates,
            model_names=self.session.available_models,
            provider_names=self.session.available_providers,
            thinking_levels=getattr(self.session, "available_thinking_levels", ()),
            theme_names=BUILTIN_TUI_THEME_NAMES,
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
    """Render the narrow working indicator that sits to the right of the prompt."""
    if not running:
        return Text("\n".join(" " for _ in range(ACTIVITY_INDICATOR_HEIGHT)))

    cycle_length = (ACTIVITY_INDICATOR_HEIGHT - 1) * 2
    cycle_position = frame % cycle_length
    active_row = (
        cycle_position
        if cycle_position < ACTIVITY_INDICATOR_HEIGHT
        else cycle_length - cycle_position
    )
    direction = 1 if cycle_position < ACTIVITY_INDICATOR_HEIGHT else -1
    trail_rows = {
        active_row: theme.highlight_background,
        active_row - direction: _blend_hex_colors(
            theme.highlight_background,
            theme.screen_background,
            fraction=0.35,
        ),
        active_row - (direction * 2): _blend_hex_colors(
            theme.highlight_background,
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


def _tree_picker_label(choice: SessionTreeChoice, *, theme: TuiTheme) -> Text:
    marker = "* " if choice.active else "  "
    label = choice.label
    indent_width = len(label) - len(label.lstrip(" "))
    indent = label[:indent_width]
    body = label[indent_width:]
    author, separator, rest = body.partition(":")
    text = Text(f"{marker}{indent}")
    if separator:
        text.append(author, style=theme.accent)
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
    return f"{provider.display_name}\n  {provider.name}"


def _subscription_login_providers(
    providers: Sequence[ProviderCatalogEntry],
) -> tuple[ProviderCatalogEntry, ...]:
    return tuple(provider for provider in providers if provider.kind == "openai-codex")


def _api_key_login_providers(
    providers: Sequence[ProviderCatalogEntry],
) -> tuple[ProviderCatalogEntry, ...]:
    return tuple(provider for provider in providers if provider.kind != "openai-codex")


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
    return command_name == "/reload"


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


def _theme_css_variables(theme: TuiTheme) -> dict[str, str]:
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
        return manager.create_session(
            cwd=cwd,
            model=selection.model,
            provider_name=selection.provider.name,
        )
    except TypeError:
        return manager.create_session(cwd=cwd, model=selection.model)


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

    for provider in settings.providers:
        if record_model in provider.models:
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
) -> None:
    """Create the default provider/session and run the Textual app."""
    if new_session and session_id is not None:
        raise RuntimeError("--resume and --new-session cannot be used together")

    provider_settings = load_provider_settings()
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
    runtime_provider_config: ProviderConfig | None = selection.provider
    try:
        provider = create_model_provider(
            selection.provider,
            model=selection.model,
            thinking_level=DEFAULT_THINKING_LEVEL,
        )
    except RuntimeError:
        startup_message = (
            "Login required. Run /login to choose a provider, "
            f"or /login {selection.provider.name} to continue with the current provider."
        )
        provider = LoginRequiredProvider(startup_message)
        runtime_provider_config = None
    session: CodingSession | None = None
    try:
        if record is None:
            record = _create_startup_session_record(
                manager,
                cwd=cwd,
                selection=selection,
            )

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
            )
        )
        app = TauTuiApp(
            session,
            tui_settings=load_tui_settings(),
            startup_message=startup_message,
            initial_prompt=initial_prompt,
        )
        await app.run_async()
    finally:
        if session is not None:
            close_session = getattr(session, "aclose", None)
            if close_session is not None:
                await close_session()
        await provider.aclose()
