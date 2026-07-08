"""Google Generative AI provider."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from json import JSONDecodeError, loads

import httpx

from tau_agent.messages import AgentMessage, AssistantMessage, UserMessage
from tau_agent.tools import AgentTool, ToolCall
from tau_agent.types import JSONValue
from tau_ai.env import OpenAICompatibleConfig
from tau_ai.events import (
    ProviderErrorEvent,
    ProviderEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderTextDeltaEvent,
    ProviderThinkingDeltaEvent,
    ProviderToolCallEvent,
)
from tau_ai.http_errors import provider_http_error_message
from tau_ai.provider import CancellationToken
from tau_ai.retry import provider_retry_event, retry_delay_seconds, wait_for_retry


class GoogleGenerativeAIProvider:
    """Provider adapter for Google's Generative Language streaming API."""

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._client = client
        self._owns_client = client is None

    async def aclose(self) -> None:
        """Close the underlying HTTP client if this provider created it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def stream_response(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Stream one Gemini response as provider-neutral events."""

        async def iterator() -> AsyncIterator[ProviderEvent]:
            client = self._get_client()
            payload = _build_google_payload(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                reasoning_effort=self._config.reasoning_effort,
                max_tokens=self._config.max_tokens,
            )
            url = (
                f"{self._config.base_url.rstrip('/')}/models/"
                f"{model}:streamGenerateContent?alt=sse&key={self._config.api_key}"
            )
            headers = {
                **dict(self._config.headers or {}),
                "content-type": "application/json",
            }

            attempt = 0
            parser = _GoogleStreamParser()
            while True:
                parser = _GoogleStreamParser()
                try:
                    async with client.stream(
                        "POST", url, json=payload, headers=headers
                    ) as response:
                        if response.status_code >= 400:
                            body = await response.aread()
                            body_text = body.decode(errors="replace")
                            if self._should_retry(attempt, status_code=response.status_code):
                                delay = retry_delay_seconds(
                                    attempt,
                                    max_delay_seconds=self._config.max_retry_delay_seconds,
                                )
                                yield provider_retry_event(
                                    attempt=attempt,
                                    max_retries=self._config.max_retries,
                                    delay_seconds=delay,
                                    reason=f"HTTP {response.status_code}",
                                    data={"status_code": response.status_code, "body": body_text},
                                )
                                attempt += 1
                                if not await wait_for_retry(delay, signal=signal):
                                    return
                                continue
                            yield ProviderErrorEvent(
                                message=provider_http_error_message(
                                    provider_name=self._config.provider_name,
                                    status_code=response.status_code,
                                    body=body_text,
                                    model=model,
                                ),
                                data={"status_code": response.status_code, "body": body_text},
                            )
                            return

                        yield ProviderResponseStartEvent(model=model)
                        async for line in response.aiter_lines():
                            if signal is not None and signal.is_cancelled():
                                return
                            event = _parse_sse_line(line)
                            if event is None:
                                continue
                            for parser_event in parser.feed(event):
                                yield parser_event
                        for parser_event in parser.finalize():
                            yield parser_event
                        return
                except httpx.HTTPError as exc:
                    if not parser.emitted_content and self._should_retry(attempt):
                        delay = retry_delay_seconds(
                            attempt,
                            max_delay_seconds=self._config.max_retry_delay_seconds,
                        )
                        yield provider_retry_event(
                            attempt=attempt,
                            max_retries=self._config.max_retries,
                            delay_seconds=delay,
                            reason="network error",
                            data={"error": str(exc), "error_type": type(exc).__name__},
                        )
                        attempt += 1
                        if not await wait_for_retry(delay, signal=signal):
                            return
                        continue
                    yield ProviderErrorEvent(message=str(exc), data={"attempts": attempt + 1})
                    return

        return iterator()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
        return self._client

    def _should_retry(self, attempt: int, *, status_code: int | None = None) -> bool:
        if attempt >= self._config.max_retries:
            return False
        return status_code is None or status_code in {408, 409, 425, 429} or status_code >= 500


class _GoogleStreamParser:
    def __init__(self) -> None:
        self.emitted_content = False
        self._content_parts: list[str] = []
        self._thinking_parts: list[str] = []
        self._tool_calls: list[ToolCall] = []
        self._finish_reason: str | None = None

    def feed(self, event: str) -> list[ProviderEvent]:
        chunk = _loads_object(event)
        if chunk is None:
            return []
        events: list[ProviderEvent] = []
        candidates = chunk.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return []
        candidate = candidates[0]
        if not isinstance(candidate, Mapping):
            return []
        finish_reason = candidate.get("finishReason")
        if isinstance(finish_reason, str):
            self._finish_reason = finish_reason
        content = candidate.get("content")
        if not isinstance(content, Mapping):
            return events
        parts = content.get("parts")
        if not isinstance(parts, list):
            return events
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                self.emitted_content = True
                if part.get("thought") is True:
                    self._thinking_parts.append(text)
                    events.append(ProviderThinkingDeltaEvent(delta=text))
                else:
                    self._content_parts.append(text)
                    events.append(ProviderTextDeltaEvent(delta=text))
            function_call = part.get("functionCall")
            if isinstance(function_call, Mapping):
                self.emitted_content = True
                default_id = f"tool-call-{len(self._tool_calls)}"
                thought_signature = part.get("thoughtSignature")
                tool_call = ToolCall(
                    id=_string_or_default(function_call.get("id"), default_id),
                    name=_string_or_default(function_call.get("name"), ""),
                    arguments=_object_or_empty(function_call.get("args")),
                    thought_signature=thought_signature
                    if isinstance(thought_signature, str)
                    else None,
                )
                self._tool_calls.append(tool_call)
                events.append(ProviderToolCallEvent(tool_call=tool_call))
        return events

    def finalize(self) -> list[ProviderEvent]:
        return [
            ProviderResponseEndEvent(
                message=AssistantMessage(
                    content="".join(self._content_parts), tool_calls=self._tool_calls
                ),
                finish_reason=_normalize_finish_reason(
                    self._finish_reason, has_tool_calls=bool(self._tool_calls)
                ),
            )
        ]


def _build_google_payload(
    *,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    reasoning_effort: str | None,
    max_tokens: int | None,
) -> dict[str, JSONValue]:
    config: dict[str, JSONValue] = {}
    payload: dict[str, JSONValue] = {
        "contents": [_message_to_google(message) for message in messages],
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if max_tokens is not None:
        config["maxOutputTokens"] = max_tokens
    thinking_config = _google_thinking_config(model, reasoning_effort)
    if thinking_config is not None:
        config["thinkingConfig"] = thinking_config
    if config:
        payload["generationConfig"] = config
    if tools:
        payload["tools"] = [{"functionDeclarations": [_tool_to_google(tool) for tool in tools]}]
    return payload


def _google_thinking_config(
    model: str, reasoning_effort: str | None
) -> dict[str, JSONValue] | None:
    if reasoning_effort is None:
        return None
    if reasoning_effort == "none":
        if _is_gemini3_pro_model(model):
            return {"thinkingLevel": "LOW"}
        if _is_gemini3_flash_model(model) or _is_gemma4_model(model):
            return {"thinkingLevel": "MINIMAL"}
        return {"thinkingBudget": 0}
    if reasoning_effort in {"MINIMAL", "LOW", "MEDIUM", "HIGH"}:
        return {"includeThoughts": True, "thinkingLevel": reasoning_effort}
    budget = _google_budget(model, reasoning_effort)
    if budget is None:
        return {"includeThoughts": True, "thinkingLevel": _google_level(model, reasoning_effort)}
    return {"includeThoughts": True, "thinkingBudget": budget}


def _google_budget(model: str, effort: str) -> int | None:
    normalized = effort.lower()
    if normalized == "xhigh":
        normalized = "high"
    if normalized not in {"minimal", "low", "medium", "high"}:
        return None
    if _is_gemini3_pro_model(model) or _is_gemini3_flash_model(model) or _is_gemma4_model(model):
        return None
    if "2.5-pro" in model:
        return {"minimal": 128, "low": 2048, "medium": 8192, "high": 32768}[normalized]
    if "2.5-flash-lite" in model:
        return {"minimal": 512, "low": 2048, "medium": 8192, "high": 24576}[normalized]
    if "2.5-flash" in model:
        return {"minimal": 128, "low": 2048, "medium": 8192, "high": 24576}[normalized]
    return -1


def _google_level(model: str, effort: str) -> str:
    normalized = effort.lower()
    if normalized == "xhigh":
        normalized = "high"
    if _is_gemini3_pro_model(model):
        return "LOW" if normalized in {"minimal", "low"} else "HIGH"
    if _is_gemma4_model(model):
        return "MINIMAL" if normalized in {"minimal", "low"} else "HIGH"
    return {
        "minimal": "MINIMAL",
        "low": "LOW",
        "medium": "MEDIUM",
        "high": "HIGH",
    }.get(normalized, "HIGH")


def _is_gemini3_pro_model(model: str) -> bool:
    return "gemini-3" in model.lower() and "pro" in model.lower()


def _is_gemini3_flash_model(model: str) -> bool:
    return "gemini-3" in model.lower() and "flash" in model.lower()


def _is_gemma4_model(model: str) -> bool:
    return "gemma-4" in model.lower() or "gemma4" in model.lower()


def _message_to_google(message: AgentMessage) -> dict[str, JSONValue]:
    if isinstance(message, UserMessage):
        return {"role": "user", "parts": [{"text": message.content}]}
    if isinstance(message, AssistantMessage):
        parts: list[JSONValue] = []
        if message.content:
            parts.append({"text": message.content})
        for tool_call in message.tool_calls:
            part: dict[str, JSONValue] = {
                "functionCall": {
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "args": dict(tool_call.arguments),
                }
            }
            if tool_call.thought_signature is not None:
                part["thoughtSignature"] = tool_call.thought_signature
            parts.append(part)
        return {"role": "model", "parts": parts or [{"text": ""}]}
    response: dict[str, JSONValue] = {
        "name": message.name,
        "response": {"output" if message.ok else "error": message.content},
    }
    if message.tool_call_id:
        response["id"] = message.tool_call_id
    return {"role": "user", "parts": [{"functionResponse": response}]}


def _tool_to_google(tool: AgentTool) -> dict[str, JSONValue]:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": _sanitize_google_schema(dict(tool.input_schema)),
    }


_UNSUPPORTED_GOOGLE_SCHEMA_KEYS = frozenset({"additionalProperties", "$schema"})


def _sanitize_google_schema(value: JSONValue) -> JSONValue:
    """Strip JSON Schema keywords Gemini's OpenAPI-subset parser rejects."""
    if isinstance(value, dict):
        return {
            key: _sanitize_google_schema(subvalue)
            for key, subvalue in value.items()
            if key not in _UNSUPPORTED_GOOGLE_SCHEMA_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_google_schema(item) for item in value]
    return value


def _parse_sse_line(line: str) -> str | None:
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    return line.removeprefix("data:").strip()


def _loads_object(value: str) -> dict[str, JSONValue] | None:
    try:
        loaded = loads(value)
    except JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _string_or_default(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _object_or_empty(value: object) -> dict[str, JSONValue]:
    return value if isinstance(value, dict) else {}


def _normalize_finish_reason(reason: str | None, *, has_tool_calls: bool) -> str:
    if has_tool_calls:
        return "tool_calls"
    if reason in {"MAX_TOKENS", "MODEL_ARMOR", "RECITATION"}:
        return "length"
    return "stop"
