"""JSONL serialization and Tau-v1 persisted-session migration."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from tau_agent.session.entries import SessionEntry

_SESSION_ENTRY_ADAPTER: TypeAdapter[SessionEntry] = TypeAdapter(SessionEntry)


class SessionJsonlError(ValueError):
    """Raised when a session JSONL line cannot be decoded."""


def entry_to_json_line(entry: SessionEntry) -> str:
    """Serialize one session entry using only the canonical Pi wire shape."""
    return _SESSION_ENTRY_ADAPTER.dump_json(entry, exclude_none=True).decode() + "\n"


def entry_from_json_line(line: str, *, line_number: int | None = None) -> SessionEntry:
    """Deserialize one entry, migrating persisted Tau-v1 messages first."""
    location = f" on line {line_number}" if line_number is not None else ""
    try:
        payload = json.loads(line)
        migrated = _migrate_session_entry(payload)
        return _SESSION_ENTRY_ADAPTER.validate_python(migrated)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise SessionJsonlError(f"Invalid session entry{location}: {exc}") from exc


def entries_from_json_lines(lines: list[str]) -> list[SessionEntry]:
    """Deserialize non-empty JSONL lines in order."""
    entries: list[SessionEntry] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        entries.append(entry_from_json_line(line, line_number=index))
    return entries


def _migrate_session_entry(value: Any) -> Any:
    """Return a canonical copy of one decoded persisted entry.

    The extension API may break in lockstep, but user session history must not.
    Migration is intentionally confined to this persistence boundary so runtime
    models and extension-facing constructors retain one strict protocol.
    """
    if not isinstance(value, dict) or value.get("type") != "message":
        return value
    migrated = dict(value)
    migrated["message"] = _migrate_message(value.get("message"))
    return migrated


def _migrate_message(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    message = dict(value)
    role = message.get("role")

    if role == "user" and ("custom_type" in message or "customType" in message):
        message["role"] = "custom"
        message["customType"] = message.pop("custom_type", message.get("customType"))
        message.pop("custom_type", None)
        message.setdefault("display", True)
        return message

    if role == "assistant":
        usage = message.get("usage")
        if isinstance(usage, dict) and usage.get("cost") is None:
            usage = dict(usage)
            usage["cost"] = {}
            message["usage"] = usage

        content = message.get("content", "")
        if isinstance(content, str):
            blocks: list[Any] = []
            if content:
                blocks.append({"type": "text", "text": content})
            blocks.extend(message.pop("tool_calls", message.pop("toolCalls", [])) or [])
            message["content"] = blocks
        elif "tool_calls" in message or "toolCalls" in message:
            blocks = list(content or [])
            blocks.extend(message.pop("tool_calls", message.pop("toolCalls", [])) or [])
            message["content"] = blocks
        return message

    if role == "tool":
        message["role"] = "toolResult"
        message["toolName"] = message.pop("name", message.get("toolName", "unknown"))
        message["toolCallId"] = message.pop("tool_call_id", message.get("toolCallId", ""))
        message["isError"] = not bool(message.pop("ok", True))
        content = message.get("content", "")
        if isinstance(content, str):
            message["content"] = [{"type": "text", "text": content}] if content else []
        data = message.pop("data", None)
        details = message.get("details")
        if isinstance(data, dict) and isinstance(details, dict):
            message["details"] = {**data, **details}
        elif details is None and data is not None:
            message["details"] = data
        error = message.pop("error", None)
        if error and not message["content"]:
            message["content"] = [{"type": "text", "text": str(error)}]
        return message

    return message
