"""Pi-style final text renderer for print mode."""

from __future__ import annotations

import typer

from tau_agent import AgentEvent, ErrorEvent, MessageEndEvent


class FinalTextRenderer:
    """Render only the final assistant text after the run finishes."""

    def __init__(self) -> None:
        self._last_assistant_text = ""
        self._failed = False
        self._error_messages: list[str] = []

    def render(self, event: AgentEvent) -> None:
        """Record events needed for final text output."""
        if isinstance(event, MessageEndEvent):
            self._last_assistant_text = event.message.content
            return

        if isinstance(event, ErrorEvent):
            if not event.recoverable:
                self._failed = True
            self._error_messages.append(event.message)

    def finish(self) -> bool:
        """Print final text or errors and return whether the run succeeded."""
        if self._failed:
            for message in self._error_messages:
                typer.echo(f"Error: {message}", err=True)
            return False

        if self._last_assistant_text:
            typer.echo(self._last_assistant_text)
        return True
