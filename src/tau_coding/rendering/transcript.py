"""Human-readable streaming transcript renderer."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.text import Text

from tau_agent import (
    AgentEndEvent,
    AgentEvent,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    RetryEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
)
from tau_coding.extensions.api import CustomMessageMarkup
from tau_coding.tui.state import format_tool_call_block


class TranscriptRenderer:
    """Render assistant deltas live and tool activity to stderr."""

    def __init__(self, *, custom_message_renderer: CustomMessageMarkup | None = None) -> None:
        self._assistant_started = False
        self._assistant_ended = False
        self._failed = False
        self._console = Console(stderr=True, highlight=False)
        self._custom_message_renderer = custom_message_renderer

    def render(self, event: AgentEvent) -> None:
        """Render one agent event."""
        if isinstance(event, MessageStartEvent):
            self._assistant_started = False
            self._assistant_ended = False
            return

        if isinstance(event, MessageDeltaEvent):
            self._assistant_started = True
            typer.echo(event.delta, nl=False)
            return

        if isinstance(event, ToolExecutionStartEvent):
            self._ensure_assistant_newline()
            self._console.print(Text(format_tool_call_block(event.tool_call), style="cyan"))
            return

        if isinstance(event, ToolExecutionUpdateEvent):
            self._ensure_assistant_newline()
            self._console.print(Text(f"… {event.message}", style="bright_black"))
            return

        if isinstance(event, RetryEvent):
            self._ensure_assistant_newline()
            self._console.print(Text(f"… {event.message}", style="bright_black"))
            return

        if isinstance(event, ToolExecutionEndEvent):
            status = "✓" if event.result.ok else "✗"
            style = "green" if event.result.ok else "red"
            self._print_tool_line(status, event.result.name, style=style)
            if event.result.content:
                self._print_tool_content(event.result.content)
            return

        if isinstance(event, ErrorEvent):
            if not event.recoverable:
                self._failed = True
            self._ensure_assistant_newline()
            self._console.print(Text(f"Error: {event.message}", style="red"))
            return

        if isinstance(event, MessageEndEvent):
            self._render_custom_message(event)
            self._ensure_assistant_newline(final=True)
            return

        if isinstance(event, AgentEndEvent):
            self._ensure_assistant_newline(final=True)

    def _render_custom_message(self, event: MessageEndEvent) -> None:
        """Render an extension custom message block via its registered renderer."""
        message = event.message
        if message.role != "user" or message.custom_type is None:
            return
        markup: str | None = None
        if self._custom_message_renderer is not None:
            markup = self._custom_message_renderer(
                message.custom_type, message.content, message.details, False
            )
        self._ensure_assistant_newline()
        if markup is None:
            self._console.print(Text(message.content))
            return
        try:
            rendered = Text.from_markup(markup)
        except Exception:  # noqa: BLE001 - malformed markup must not crash print mode
            rendered = Text(markup)
        self._console.print(rendered)

    def finish(self) -> bool:
        """Return whether the rendered run succeeded."""
        return not self._failed

    def _ensure_assistant_newline(self, *, final: bool = False) -> None:
        if self._assistant_started and not self._assistant_ended:
            typer.echo()
            self._assistant_ended = True
        elif final and not self._assistant_started:
            self._assistant_ended = True

    def _print_tool_line(
        self,
        marker: str,
        name: str,
        detail: str | None = None,
        *,
        style: str,
    ) -> None:
        line = Text()
        line.append(marker, style=style)
        line.append(f" {name}", style=style)
        if detail:
            line.append(f" {detail}", style="bright_black")
        self._console.print(line)

    def _print_tool_content(self, content: str) -> None:
        for line in content.splitlines() or [""]:
            self._console.print(Text(f"  {line}", style="white"))
