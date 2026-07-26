"""Structured diagnostic logging for coding-session failures."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tau_agent.messages import AssistantMessage
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

    def log_assistant_error(
        self,
        *,
        context: AgentCallDiagnosticContext,
        phase: str,
        message: AssistantMessage,
    ) -> Path:
        """Log a terminal assistant error message with safe diagnostic details."""
        entry = _base_entry(context, phase=phase, kind="assistant_error")
        error: dict[str, Any] = {
            "message": message.error_message or "Error",
            "stop_reason": message.stop_reason,
        }
        provider = _provider_error_details(message)
        if provider:
            error["provider"] = provider
        entry["error"] = error
        self._append(entry)
        return self.path

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, sort_keys=True) + "\n")


def new_agent_call_run_id() -> str:
    """Return a stable id for one coding-session agent call."""
    return uuid4().hex


_SAFE_ERROR_OBJECT_KEYS = ("type", "code", "message", "param")


def _provider_error_details(message: AssistantMessage) -> dict[str, Any]:
    """Extract non-secret provider failure details from message diagnostics.

    Provider adapters attach the raw stream event to assistant diagnostics, but
    that payload can be large. Only scalar classification fields (status codes,
    attempt counts, and error type/code/message) are copied into the log so the
    entry stays small and free of request or credential material.
    """
    for diagnostic in message.diagnostics or []:
        if diagnostic.type != "provider_error" or not diagnostic.details:
            continue
        details: dict[str, Any] = {}
        status_code = diagnostic.details.get("status_code")
        if isinstance(status_code, int) and not isinstance(status_code, bool):
            details["status_code"] = status_code
        attempts = diagnostic.details.get("attempts")
        if isinstance(attempts, int) and not isinstance(attempts, bool):
            details["attempts"] = attempts
        event = diagnostic.details.get("event")
        if isinstance(event, dict):
            event_details = _safe_stream_event_details(event)
            if event_details:
                details["event"] = event_details
        return details
    return {}


def _safe_stream_event_details(event: dict[str, Any]) -> dict[str, Any]:
    """Keep only non-secret scalar fields from a provider stream error event."""
    details: dict[str, Any] = {}
    event_type = event.get("type")
    if isinstance(event_type, str) and event_type:
        details["type"] = event_type
    sequence_number = event.get("sequence_number")
    if isinstance(sequence_number, int) and not isinstance(sequence_number, bool):
        details["sequence_number"] = sequence_number
    nested = _safe_error_object(event.get("error"))
    if nested:
        details["error"] = nested
    response = event.get("response")
    if isinstance(response, dict):
        response_error = _safe_error_object(response.get("error"))
        if response_error:
            details["response_error"] = response_error
    return details


def _safe_error_object(value: object) -> dict[str, str]:
    """Copy scalar classification fields from a provider error object."""
    if not isinstance(value, dict):
        return {}
    return {
        key: field
        for key in _SAFE_ERROR_OBJECT_KEYS
        if isinstance((field := value.get(key)), str) and field
    }


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
