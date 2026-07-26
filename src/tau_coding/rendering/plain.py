"""Pi-style final text renderer for print mode."""

import typer

from tau_agent.events import MessageEndEvent
from tau_agent.messages import AssistantMessage
from tau_coding.events import CodingSessionEvent


class FinalTextRenderer:
    def __init__(self) -> None:
        self._last_assistant_text = ""
        self._failed = False
        self._error_messages: list[str] = []

    def render(self, event: CodingSessionEvent) -> None:
        if not isinstance(event, MessageEndEvent) or not isinstance(
            event.message, AssistantMessage
        ):
            return
        self._last_assistant_text = event.message.text
        if event.message.stop_reason in {"error", "aborted"}:
            self._failed = event.message.stop_reason == "error"
            if event.message.error_message:
                self._error_messages.append(event.message.error_message)

    def finish(self) -> bool:
        if self._failed:
            for message in self._error_messages:
                typer.echo(f"Error: {message}", err=True)
            return False
        if self._last_assistant_text:
            typer.echo(self._last_assistant_text)
        return True
