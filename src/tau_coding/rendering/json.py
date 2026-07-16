"""Pi-compatible JSON event stream renderer."""

import typer

from tau_agent.events import MessageEndEvent
from tau_agent.messages import AssistantMessage
from tau_coding.events import CodingSessionEvent


class JsonEventRenderer:
    def __init__(self) -> None:
        self._failed = False

    def render(self, event: CodingSessionEvent) -> None:
        if (
            isinstance(event, MessageEndEvent)
            and isinstance(event.message, AssistantMessage)
            and event.message.stop_reason == "error"
        ):
            self._failed = True
        typer.echo(event.model_dump_json(by_alias=True, exclude_none=True))

    def finish(self) -> bool:
        return not self._failed
