"""Human-readable streaming transcript renderer."""

import typer

from tau_agent import (
    AgentEndEvent,
    AgentEvent,
    ErrorEvent,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
)


class TranscriptRenderer:
    """Render assistant deltas live and tool activity to stderr."""

    def __init__(self) -> None:
        self._assistant_started = False
        self._assistant_ended = False
        self._failed = False

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
            typer.echo(f"→ {event.tool_call.name} {event.tool_call.arguments}", err=True)
            return

        if isinstance(event, ToolExecutionEndEvent):
            status = "✓" if event.result.ok else "✗"
            typer.echo(f"{status} {event.result.name}", err=True)
            if not event.result.ok and event.result.content:
                typer.echo(event.result.content, err=True)
            return

        if isinstance(event, ErrorEvent):
            if not event.recoverable:
                self._failed = True
            self._ensure_assistant_newline()
            typer.echo(f"Error: {event.message}", err=True)
            return

        if isinstance(event, MessageEndEvent | AgentEndEvent):
            self._ensure_assistant_newline(final=True)

    def finish(self) -> bool:
        """Return whether the rendered run succeeded."""
        return not self._failed

    def _ensure_assistant_newline(self, *, final: bool = False) -> None:
        if self._assistant_started and not self._assistant_ended:
            typer.echo()
            self._assistant_ended = True
        elif final and not self._assistant_started:
            self._assistant_ended = True
