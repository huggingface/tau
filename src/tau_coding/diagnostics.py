"""Structured diagnostic logging for coding-session failures."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tau_agent.events import ErrorEvent
from tau_coding.paths import TauPaths


@dataclass(frozen=True, slots=True)
class AgentCallDiagnosticContext:
    """Non-secret context attached to an agent-call diagnostic entry."""

    provider_name: str
    model: str
    cwd: Path
    session_id: str | None
    run_id: str


class AgentCallDiagnosticLogger:
    """Append structured JSONL diagnostics for agent-call failures."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def from_paths(cls, paths: TauPaths | None = None) -> AgentCallDiagnosticLogger:
        """Create a logger using Tau's default path layout."""
        return cls((paths or TauPaths()).agent_calls_log_path)

    def log_exception(
        self,
        *,
        context: AgentCallDiagnosticContext,
        phase: str,
        exc: BaseException,
    ) -> Path:
        """Log an unexpected exception with traceback and return the log path."""
        entry = _base_entry(context, phase=phase, kind="exception")
        entry["exception"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        self._append(entry)
        return self.path

    def log_error_event(
        self,
        *,
        context: AgentCallDiagnosticContext,
        phase: str,
        event: ErrorEvent,
    ) -> Path:
        """Log an agent error event with safe provider diagnostic details."""
        entry = _base_entry(context, phase=phase, kind="error_event")
        entry["error"] = {
            "message": event.message,
            "recoverable": event.recoverable,
        }
        if event.data is not None:
            entry["error"]["data"] = event.data
        self._append(entry)
        return self.path

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, sort_keys=True) + "\n")


def new_agent_call_run_id() -> str:
    """Return a stable id for one coding-session agent call."""
    return uuid4().hex


def _base_entry(
    context: AgentCallDiagnosticContext,
    *,
    phase: str,
    kind: str,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "kind": kind,
        "phase": phase,
        "run_id": context.run_id,
        "session_id": context.session_id,
        "provider_name": context.provider_name,
        "model": context.model,
        "cwd": str(context.cwd),
    }
