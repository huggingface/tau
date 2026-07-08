"""JSON event stream renderer."""

from __future__ import annotations

import typer

from tau_agent import AgentEvent, ErrorEvent


class JsonEventRenderer:
    """Render every agent event as one JSON object per line."""

    def __init__(self) -> None:
        self._failed = False

    def render(self, event: AgentEvent) -> None:
        """Write one event as JSONL."""
        if isinstance(event, ErrorEvent) and not event.recoverable:
            self._failed = True
        typer.echo(event.model_dump_json())

    def finish(self) -> bool:
        """Return whether the rendered run succeeded."""
        return not self._failed
