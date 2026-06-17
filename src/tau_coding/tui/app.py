"""Minimal Textual app for Tau coding sessions."""

from pathlib import Path
from typing import Protocol, cast

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.widgets import Footer, Header, Input, Static
from textual.worker import Worker

from tau_ai import OpenAICompatibleProvider
from tau_coding.commands import CommandRegistry, create_default_command_registry
from tau_coding.provider_config import (
    load_provider_settings,
    openai_compatible_config_from_provider,
    resolve_provider_selection,
)
from tau_coding.session import CodingSession, CodingSessionConfig, jsonl_session_storage
from tau_coding.session_manager import SessionManager
from tau_coding.tui.adapter import TuiEventAdapter
from tau_coding.tui.autocomplete import CompletionState, build_completion_state
from tau_coding.tui.state import TuiState
from tau_coding.tui.widgets import SessionSidebar, TranscriptView, render_completion_suggestions


class CompletionActionTarget(Protocol):
    """App actions used by the prompt input completion bindings."""

    def action_accept_completion(self) -> None: ...

    def action_completion_next(self) -> None: ...

    def action_completion_previous(self) -> None: ...

    def action_open_command_palette(self) -> None: ...


class PromptInput(Input):
    """Prompt input with completion key bindings."""

    BINDINGS = [
        Binding("ctrl+k", "open_command_palette", show=False, priority=True),
        Binding("tab", "accept_completion", show=False, priority=True),
        Binding("down", "completion_next", show=False, priority=True),
        Binding("up", "completion_previous", show=False, priority=True),
    ]

    def action_accept_completion(self) -> None:
        """Accept the selected app-level completion."""
        self._completion_target().action_accept_completion()

    def action_completion_next(self) -> None:
        """Select the next app-level completion."""
        self._completion_target().action_completion_next()

    def action_completion_previous(self) -> None:
        """Select the previous app-level completion."""
        self._completion_target().action_completion_previous()

    def action_open_command_palette(self) -> None:
        """Open the app-level command palette."""
        self._completion_target().action_open_command_palette()

    def action_scroll_down(self) -> None:
        """Use down arrow for completion selection while focused."""
        self._completion_target().action_completion_next()

    def action_scroll_up(self) -> None:
        """Use up arrow for completion selection while focused."""
        self._completion_target().action_completion_previous()

    def on_key(self, event: Key) -> None:
        """Route completion keys before default input handling."""
        if event.key == "tab":
            event.stop()
            self._completion_target().action_accept_completion()
        elif event.key == "ctrl+k":
            event.stop()
            self._completion_target().action_open_command_palette()
        elif event.key == "down":
            event.stop()
            self._completion_target().action_completion_next()
        elif event.key == "up":
            event.stop()
            self._completion_target().action_completion_previous()

    def _completion_target(self) -> CompletionActionTarget:
        return cast(CompletionActionTarget, self.app)


class TauTuiApp(App[None]):
    """Interactive Textual frontend for a ``CodingSession``."""

    TITLE = "Tau"
    CSS = """
    Screen {
        layout: vertical;
        background: #0f1117;
        color: #e6edf3;
    }

    Header {
        background: #161b22;
        color: #f0f6fc;
        dock: top;
    }

    Footer {
        background: #161b22;
        color: #8b949e;
    }

    #status {
        height: 1;
        padding: 0 1;
        background: #0f1117;
        color: #8b949e;
    }

    #workspace {
        height: 1fr;
    }

    #sidebar {
        width: 32;
        min-width: 28;
        padding: 1;
        background: #161b22;
        border-right: solid #30363d;
    }

    #main-pane {
        width: 1fr;
        padding: 1 1 0 1;
    }

    #transcript {
        height: 1fr;
        border: round #30363d;
        background: #0d1117;
        padding: 0 1;
    }

    #prompt {
        background: #0d1117;
        color: #f0f6fc;
        border: round #238636;
        margin: 0 1 0 1;
    }

    #autocomplete {
        height: auto;
        max-height: 6;
        margin: 0 1 1 1;
        padding: 0 1;
        background: #161b22;
        color: #e6edf3;
        border: tall #30363d;
    }
    """
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        Binding("ctrl+k", "open_command_palette", "Commands"),
        Binding("tab", "accept_completion", "Complete", priority=True),
        Binding("down", "completion_next", "Next completion", priority=True),
        Binding("up", "completion_previous", "Previous completion", priority=True),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, session: CodingSession) -> None:
        super().__init__()
        self.session = session
        self.state = TuiState()
        self.state.load_messages(session.messages)
        self.adapter = TuiEventAdapter(self.state)
        self._prompt_worker: Worker[None] | None = None
        self._completion_state = CompletionState()

    def compose(self) -> ComposeResult:
        """Compose the TUI widgets."""
        yield Header()
        yield Static("Ready", id="status")
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
                yield PromptInput(placeholder="Ask Tau…", id="prompt")
                yield Static("", id="autocomplete")
        yield Footer()

    async def on_mount(self) -> None:
        """Focus the prompt when the app starts."""
        self.query_one(Input).focus()
        self._refresh()
        self._refresh_completions()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Update prompt autocomplete when the input value changes."""
        if event.input.id != "prompt":
            return
        self._completion_state = self._build_completion_state(event.value)
        self._refresh_completions()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle a submitted prompt or slash command."""
        text = event.value.strip()
        event.input.value = ""
        self._completion_state = CompletionState()
        self._refresh_completions()
        if not text:
            return

        command = self.session.handle_command(text)
        if command.handled:
            if command.clear_requested:
                self.state.clear()
            if command.compact_summary is not None:
                try:
                    compact_message = await self.session.compact(command.compact_summary)
                    self.state.add_item("status", compact_message)
                except Exception as exc:  # noqa: BLE001 - surface command failures in the TUI
                    self.state.add_item("error", f"Error: {exc}")
            if command.message:
                self.state.add_item("status", command.message)
            self._refresh()
            if command.exit_requested:
                self.exit()
            return

        if self.state.running:
            self.state.add_item("status", "Tau is already working. Press Escape to cancel.")
            self._refresh()
            return

        self.state.add_item("user", text)
        self._refresh()
        self._prompt_worker = self.run_worker(self._run_prompt(text), exclusive=True)

    async def _run_prompt(self, text: str) -> None:
        """Run one prompt and stream session events into the TUI state."""
        try:
            async for event in self.session.prompt(text):
                self.adapter.apply(event)
                self._refresh()
        except Exception as exc:  # noqa: BLE001 - surface unexpected worker errors in the TUI
            self.state.error = str(exc)
            self.state.add_item("error", f"Error: {exc}")
            self.state.running = False
            self._refresh()

    def action_cancel(self) -> None:
        """Cancel the active agent turn."""
        if self.state.running:
            self.session.cancel()
            self.state.add_item("status", "Cancellation requested.")
        else:
            self.state.add_item("status", "Nothing to cancel.")
        self._refresh()

    def action_accept_completion(self) -> None:
        """Accept the currently selected prompt completion."""
        item = self._completion_state.selected
        if item is None:
            return
        prompt = self.query_one("#prompt", Input)
        prompt.value = item.apply(prompt.value)
        prompt.cursor_position = len(prompt.value)
        self._completion_state = self._build_completion_state(prompt.value)
        self._refresh_completions()

    def action_completion_next(self) -> None:
        """Select the next prompt completion."""
        if not self._completion_state.items:
            return
        self._completion_state = self._completion_state.select_next()
        self._refresh_completions()

    def action_completion_previous(self) -> None:
        """Select the previous prompt completion."""
        if not self._completion_state.items:
            return
        self._completion_state = self._completion_state.select_previous()
        self._refresh_completions()

    def action_open_command_palette(self) -> None:
        """Open the slash-command palette in the prompt."""
        prompt = self.query_one("#prompt", Input)
        prompt.focus()
        prompt.value = "/"
        prompt.cursor_position = len(prompt.value)
        self._completion_state = self._build_completion_state(prompt.value)
        self._refresh_completions()

    def _refresh(self) -> None:
        sidebar = self.query_one("#sidebar", SessionSidebar)
        sidebar.update_from_session(self.session)
        transcript = self.query_one("#transcript", TranscriptView)
        transcript.update_from_state(self.state)
        status = self.query_one("#status", Static)
        status.update("Working…" if self.state.running else "Ready")

    def _refresh_completions(self) -> None:
        suggestions = self.query_one("#autocomplete", Static)
        suggestions.display = bool(self._completion_state.items)
        suggestions.update(render_completion_suggestions(self._completion_state))

    def _build_completion_state(self, text: str) -> CompletionState:
        registry = _session_command_registry(self.session)
        return build_completion_state(
            text,
            command_registry=registry,
            skills=self.session.skills,
            prompt_templates=self.session.prompt_templates,
        )


def _session_command_registry(session: CodingSession) -> CommandRegistry:
    registry = getattr(session, "command_registry", None)
    if isinstance(registry, CommandRegistry):
        return registry
    return create_default_command_registry()


async def run_tui_app(
    *,
    model: str | None,
    cwd: Path,
    session_id: str | None = None,
    new_session: bool = False,
    provider_name: str | None = None,
    auto_compact_token_threshold: int | None = None,
    session_manager: SessionManager | None = None,
) -> None:
    """Create the default provider/session and run the Textual app."""
    if new_session and session_id is not None:
        raise RuntimeError("--resume and --new-session cannot be used together")

    provider_settings = load_provider_settings()
    selection = resolve_provider_selection(
        provider_settings,
        provider_name=provider_name,
        model=model,
    )
    provider = OpenAICompatibleProvider(openai_compatible_config_from_provider(selection.provider))
    manager = session_manager or SessionManager()
    session: CodingSession | None = None
    try:
        if session_id is not None:
            existing_record = manager.get_session(session_id)
            if existing_record is None:
                raise RuntimeError(f"Unknown session: {session_id}")
            record = existing_record
        else:
            record = manager.create_session(cwd=cwd, model=selection.model)

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
                auto_compact_token_threshold=auto_compact_token_threshold,
            )
        )
        app = TauTuiApp(session)
        await app.run_async()
    finally:
        if session is not None:
            close_session = getattr(session, "aclose", None)
            if close_session is not None:
                await close_session()
        await provider.aclose()
