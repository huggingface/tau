"""Opt-in provider request/response observation primitives."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal, Protocol

from tau_agent.types import JSONValue

LLMObservationKind = Literal["request", "response", "error"]

_REDACTED = "[REDACTED]"
_SENSITIVE_HEADER_PARTS = (
    "authorization",
    "api-key",
    "apikey",
    "cookie",
    "credential",
    "secret",
    "session",
    "token",
    "chatgpt-account-id",
)
_SENSITIVE_SCALAR_KEYS = {
    "arguments",
    "body",
    "content",
    "delta",
    "description",
    "instructions",
    "message",
    "output",
    "partial_json",
    "refusal",
    "system",
    "text",
    "thinking",
}
_SENSITIVE_SUBTREE_KEYS = {"input"}
_STRUCTURAL_STRING_KEYS = {
    "call_id",
    "finish_reason",
    "id",
    "model",
    "name",
    "role",
    "status",
    "strict",
    "tool_choice",
    "tool_use_id",
    "type",
}


@dataclass(frozen=True, slots=True)
class LLMObservation:
    """A redacted observation of one provider HTTP request lifecycle point."""

    kind: LLMObservationKind
    provider: str
    model: str
    method: str
    url: str
    attempt: int
    stream: bool
    data: dict[str, JSONValue]

    def to_json(self) -> dict[str, JSONValue]:
        """Return a JSON-serializable representation."""
        return {
            "kind": self.kind,
            "provider": self.provider,
            "model": self.model,
            "method": self.method,
            "url": self.url,
            "attempt": self.attempt,
            "stream": self.stream,
            "data": self.data,
        }


class LLMObserver(Protocol):
    """Receives provider-layer observations without owning output policy."""

    def record(self, observation: LLMObservation) -> None:
        """Record one redacted provider observation."""
        ...


def observe_llm_request(
    observer: LLMObserver | None,
    *,
    provider: str,
    model: str,
    method: str,
    url: str,
    headers: Mapping[str, object],
    body: object,
    attempt: int,
    stream: bool,
) -> None:
    """Emit a redacted provider request observation, if enabled."""
    _record_safely(
        observer,
        LLMObservation(
            kind="request",
            provider=provider,
            model=model,
            method=method,
            url=url,
            attempt=attempt,
            stream=stream,
            data={
                "request": {
                    "headers": redact_headers(headers),
                    "body": redact_json_value(body),
                }
            },
        ),
    )


def observe_llm_response(
    observer: LLMObserver | None,
    *,
    provider: str,
    model: str,
    method: str,
    url: str,
    status_code: int,
    headers: Mapping[str, object],
    attempt: int,
    stream: bool,
) -> None:
    """Emit a redacted provider response-metadata observation, if enabled."""
    _record_safely(
        observer,
        LLMObservation(
            kind="response",
            provider=provider,
            model=model,
            method=method,
            url=url,
            attempt=attempt,
            stream=stream,
            data={
                "response": {
                    "status_code": status_code,
                    "headers": redact_headers(headers),
                }
            },
        ),
    )


def observe_llm_error(
    observer: LLMObserver | None,
    *,
    provider: str,
    model: str,
    method: str,
    url: str,
    attempt: int,
    stream: bool,
    error: Mapping[str, object],
) -> None:
    """Emit a redacted provider error observation, if enabled."""
    _record_safely(
        observer,
        LLMObservation(
            kind="error",
            provider=provider,
            model=model,
            method=method,
            url=url,
            attempt=attempt,
            stream=stream,
            data={"error": redact_json_value(error)},
        ),
    )


def redact_headers(headers: Mapping[str, object]) -> dict[str, JSONValue]:
    """Return headers with known credential-bearing values removed."""
    redacted: dict[str, JSONValue] = {}
    for key, value in headers.items():
        name = str(key)
        if _is_sensitive_header(name):
            redacted[name] = _REDACTED
        else:
            redacted[name] = str(value)
    return redacted


def redact_json_value(
    value: object,
    *,
    key: str | None = None,
    redact_subtree_strings: bool = False,
) -> JSONValue:
    """Redact secret-prone text while preserving JSON structure."""
    normalized_key = key.lower() if key is not None else None
    child_redacts = redact_subtree_strings or normalized_key in _SENSITIVE_SUBTREE_KEYS

    if isinstance(value, Mapping):
        return {
            str(item_key): redact_json_value(
                item_value,
                key=str(item_key),
                redact_subtree_strings=child_redacts,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple):
        return [
            redact_json_value(
                item,
                key=key,
                redact_subtree_strings=child_redacts,
            )
            for item in value
        ]
    if isinstance(value, str):
        if _should_redact_string(normalized_key, child_redacts):
            return _redacted_text(value)
        return value
    if value is None or isinstance(value, bool | int | float):
        return value
    return _redacted_text(str(value))


def _record_safely(observer: LLMObserver | None, observation: LLMObservation) -> None:
    if observer is None:
        return
    try:
        observer.record(observation)
    except Exception:
        return


def _is_sensitive_header(name: str) -> bool:
    normalized = name.lower()
    return any(part in normalized for part in _SENSITIVE_HEADER_PARTS)


def _should_redact_string(key: str | None, redact_subtree_strings: bool) -> bool:
    if key is not None and key in _SENSITIVE_SCALAR_KEYS:
        return True
    return redact_subtree_strings and key not in _STRUCTURAL_STRING_KEYS


def _redacted_text(value: str) -> dict[str, JSONValue]:
    return {
        "redacted": True,
        "kind": "text",
        "length": len(value),
        "sha256": sha256(value.encode("utf-8")).hexdigest(),
    }
