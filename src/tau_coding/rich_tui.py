"""Minimal interactive frontend built with Rich and prompt_toolkit.

This frontend intentionally keeps a smaller surface than the Textual app: one
transcript, one prompt, one contextual status line, and completion for commands.
Detailed pickers and extension-owned widgets remain Textual-only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from tau_coding.session import CodingSession, parse_terminal_command
from tau_coding.tui.adapter import TuiEventAdapter
from tau_coding.tui.config import TAU_DARK_THEME, TuiTheme
from tau_coding.tui.state import TuiState, format_terminal_command_result_block
from tau_coding.tui.terminal_title import TerminalTitleController
from tau_coding.tui.widgets import render_chat_item


class RichTuiCompleter(Completer):
    """Complete the deliberately small public input vocabulary."""

    def __init__(self, session: CodingSession) -> None:
        self.session = session

    def get_completions(self, document: Document, complete_event: object) -> Any:
        del complete_event
        text = document.text_before_cursor
        if not text or any(character.isspace() for character in text):
            return
        candidates = [
            f"/{command.name}" for command in self.session.command_registry.list_commands()
        ]
        candidates.extend(f"/skill:{skill.name}" for skill in self.session.skills)
        candidates.extend(template.name for template in self.session.prompt_templates)
        for candidate in sorted(set(candidates)):
            if candidate.startswith(text):
                yield Completion(candidate, start_position=-len(text))


@dataclass(slots=True)
class RichTuiRenderer:
    """Turn Textual-free display state into one stable Rich layout."""

    session: CodingSession
    state: TuiState
    theme: TuiTheme = TAU_DARK_THEME
    notice: str | None = None

    def layout(self) -> Layout:
        root = Layout(name="root", size=max(4, Console().height - 3))
        sections = [Layout(self._transcript(), name="transcript", ratio=1)]
        queue_height = self._queue_height()
        if queue_height:
            sections.append(Layout(self._queue(), name="queue", size=queue_height))
        sections.append(Layout(self._status(), name="status", size=1))
        root.split_column(*sections)
        return root

    def _transcript(self) -> RenderableType:
        rows: list[RenderableType] = []
        if self.notice:
            rows.extend((Text(self.notice, style=self.theme.muted_text), Text("")))
        for item in self.state.items:
            if item.role == "thinking" and not self.state.show_thinking:
                continue
            rows.append(
                render_chat_item(
                    item,
                    theme=self.theme,
                    show_tool_results=self.state.show_tool_results,
                    custom_markup=self.state.resolve_custom_markup(
                        item, expanded=self.state.show_tool_results
                    ),
                )
            )
        if self.state.assistant_buffer:
            rows.append(Markdown(self.state.assistant_buffer))
        if not rows:
            rows.append(
                Align.center(
                    Text("τ\nAsk a question or type /hotkeys", style=self.theme.muted_text),
                    vertical="middle",
                )
            )
        return Align.left(Group(*rows), vertical="bottom")

    def _queue(self) -> RenderableType:
        lines = [f"↪ steer: {text}" for text in self.state.queued_steering]
        lines.extend(f"↪ follow-up: {text}" for text in self.state.queued_follow_up)
        return Text("\n".join(lines), style=self.theme.muted_text)

    def _queue_height(self) -> int:
        return min(4, self.state.queued_message_count) if self.state.queued_message_count else 0

    def _status(self) -> RenderableType:
        branch = _git_branch_label(self.session)
        activity = "working" if self.state.running else "ready"
        context = _context_percent(self.session)
        left = f" {self.session.cwd.name}{branch}"
        right = (
            f"{activity} · {self.session.provider_name}:{self.session.model} · "
            f"{self.session.thinking_level} · ctx {context} "
        )
        width = max(1, Console().width)
        gap = max(1, width - len(left) - len(right))
        return Text(left + (" " * gap) + right, style=self.theme.muted_text)


def _git_branch_label(session: CodingSession) -> str:
    """Return a cheap branch label without making startup depend on git."""
    head = session.cwd / ".git" / "HEAD"
    try:
        value = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return f":{value.rpartition('/')[-1]}" if value.startswith("ref:") else ""


def _context_percent(session: CodingSession) -> str:
    window = session.context_window_tokens
    if window <= 0:
        return "?"
    return f"{min(999, round(session.context_token_estimate / window * 100))}%"


class RichPromptTui:
    """A transcript-first Tau frontend with no persistent sidebar or help bar."""

    def __init__(
        self,
        session: CodingSession,
        *,
        startup_notices: Sequence[str] = (),
        initial_prompt: str | None = None,
    ) -> None:
        self.session = session
        self.state = TuiState(skills=session.skills)
        self.state.load_messages(session.messages)
        self.adapter = TuiEventAdapter(self.state)
        self.renderer = RichTuiRenderer(
            session,
            self.state,
            notice="\n".join(startup_notices) or None,
        )
        self.initial_prompt = initial_prompt
        self.console = Console()
        self._title = TerminalTitleController()
        self._exit = False
        self._live: Live | None = None
        self._history = InMemoryHistory()
        for message in session.messages:
            text = getattr(message, "text", None)
            if isinstance(text, str) and text:
                self._history.append_string(text)
        self._prompt = PromptSession[str](
            history=self._history,
            completer=RichTuiCompleter(session),
            complete_while_typing=True,
            multiline=True,
            key_bindings=self._keybindings(),
            bottom_toolbar=self._toolbar,
        )

    def _keybindings(self) -> KeyBindings:
        bindings = KeyBindings()

        @bindings.add("escape", "enter")
        def _submit(event: Any) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("c-d")
        def _quit(event: Any) -> None:
            if event.current_buffer.text:
                event.current_buffer.delete()
            else:
                event.app.exit(exception=EOFError)

        return bindings

    def _toolbar(self) -> ANSI:
        return ANSI(
            "\x1b[2m Alt+Enter submit · Enter newline · Tab complete · "
            "Ctrl+C cancel input · Ctrl+D quit \x1b[0m"
        )

    async def run(self) -> None:
        """Run until EOF or /quit, keeping all session ownership outside the UI."""
        await self.session.emit_pending_session_start()
        pending = self.initial_prompt
        try:
            with patch_stdout(raw=True):
                while not self._exit:
                    self._show_static_transcript()
                    try:
                        text = (
                            pending
                            if pending is not None
                            else await self._prompt.prompt_async("τ ")
                        )
                        pending = None
                    except (EOFError, KeyboardInterrupt):
                        break
                    text = text.strip()
                    if text:
                        self._start_live_transcript()
                        try:
                            await self._submit(text)
                        finally:
                            self._stop_live_transcript()
        finally:
            self._title.restore()
            self._stop_live_transcript()

    def _show_static_transcript(self) -> None:
        """Render a stable frame before prompt_toolkit takes control of input."""
        self.console.clear()
        self.console.print(self.renderer.layout())

    def _start_live_transcript(self) -> None:
        """Let Rich own the terminal only while output is actively changing."""
        self.console.clear()
        self._live = Live(
            self.renderer.layout(),
            console=self.console,
            screen=False,
            auto_refresh=False,
            vertical_overflow="crop",
        )
        self._live.start(refresh=True)

    def _stop_live_transcript(self) -> None:
        live = self._live
        self._live = None
        if live is not None:
            live.stop()

    async def _submit(self, text: str) -> None:
        terminal = parse_terminal_command(text)
        if terminal is not None:
            result = await self.session.run_terminal_command(
                terminal.command,
                add_to_context=terminal.add_to_context,
            )
            self.state.add_item("user", f"$ {terminal.command}")
            self.state.add_item(
                "tool",
                format_terminal_command_result_block(
                    ok=result.ok,
                    added_to_context=terminal.add_to_context,
                    output=result.output,
                ),
                always_show_tool_result=True,
            )
            self._refresh()
            return

        command = self.session.handle_command(text)
        if command.handled:
            await self._apply_command(command)
            return

        try:
            async for event in self.session.prompt(text):
                self.adapter.apply(event)
                self._title.update(
                    getattr(self.session, "session_title", None),
                    running=self.state.running,
                )
                self._refresh()
                await asyncio.sleep(0)
        except KeyboardInterrupt:
            self.session.cancel()
            self.state.add_item("status", "Cancelled")
        finally:
            self._refresh()

    async def _apply_command(self, command: Any) -> None:
        if command.exit_requested:
            self._exit = True
        elif command.clear_requested:
            self.state.clear()
        elif command.new_session_requested:
            self.state.clear()
            command = _with_message(command, await self.session.new_session())
        elif command.compact_summary is not None:
            command = _with_message(command, await self.session.compact(command.compact_summary))
        elif command.resume_session_id is not None:
            command = _with_message(command, await self.session.resume(command.resume_session_id))
            self.state.clear()
            self.state.load_messages(self.session.messages)
        elif command.thinking_level is not None:
            command = _with_message(
                command, await self.session.set_thinking_level(command.thinking_level)
            )
        elif any(
            (
                command.resume_picker_requested,
                command.tree_picker_requested,
                command.login_picker_requested,
                command.custom_provider_login_requested,
                command.logout_picker_requested,
                command.model_picker_requested,
                command.scoped_models_picker_requested,
                command.theme_picker_requested,
            )
        ):
            command = _with_message(
                command,
                "This minimalist frontend has no picker. Supply the command argument or "
                "restart with --tui textual.",
            )
        if command.message:
            self.renderer.notice = command.message
        self._refresh()

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self.renderer.layout(), refresh=True)


def _with_message(command: Any, message: str) -> Any:
    """Avoid coupling this frontend to every CommandResult constructor field."""
    from dataclasses import replace

    return replace(command, message=message)


async def run_rich_tui(
    session: CodingSession,
    *,
    startup_notices: Sequence[str] = (),
    initial_prompt: str | None = None,
) -> None:
    """Run the built-in Rich/prompt_toolkit frontend."""
    app = RichPromptTui(
        session,
        startup_notices=startup_notices,
        initial_prompt=initial_prompt,
    )
    with suppress(EOFError):
        await app.run()
