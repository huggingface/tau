"""Shared event-rendering primitives for Tau coding modes."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from tau_agent import AgentEvent


class PrintOutputMode(StrEnum):
    """Output modes supported by non-interactive print mode."""

    text = "text"
    json = "json"
    transcript = "transcript"


class EventRenderer(Protocol):
    """Consumes agent events and renders them for a frontend or output mode."""

    def render(self, event: AgentEvent) -> None:
        """Render one event."""

    def finish(self) -> bool:
        """Finish rendering and return whether the run succeeded."""
