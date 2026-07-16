"""OpenAI Codex subscription Responses provider."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from json import JSONDecodeError, dumps, loads
from platform import machine, release, system
from typing import Any

import httpx

from tau_agent.messages import (
    AgentMessage,
    AssistantMessage,
    ToolResultMessage,
    Usage,
    UserMessage,
    assistant_content,
)
from tau_agent.tools import AgentTool, ToolCall
from tau_agent.types import JSONValue
from tau_ai._provider_events import (
    ProviderErrorEvent,
    ProviderEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderTextDeltaEvent,
    ProviderThinkingDeltaEvent,
    ProviderToolCallEvent,
)
from tau_ai.env import (
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
)
from tau_ai.events import AssistantMessageEvent
from tau_ai.http import create_async_client
from tau_ai.http_errors import provider_http_error_message
from tau_ai.provider import CancellationToken
from tau_ai.retry import provider_retry_event, retry_delay_seconds, wait_for_retry
from tau_ai.stream import canonicalize_provider_stream

DEFAULT_OPENAI_CODEX_BASE_URL = "https://chatgpt.com/backend-api"


@dataclass(frozen=True, slots=True)
class OpenAICodexCredentials:
    """Bearer token and account id required by ChatGPT Codex Responses."""

    access_token: str
    account_id: str


type OpenAICodexCredentialResolver = Callable[[], Awaitable[OpenAICodexCredentials]]


@dataclass(frozen=True, slots=True)
class OpenAICodexConfig:
    """Configuration for the OpenAI Codex subscription Responses endpoint."""

    credential_resolver: OpenAICodexCredentialResolver
    base_url: str = DEFAULT_OPENAI_CODEX_BASE_URL
    headers: Mapping[str, str] | None = None
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS
    originator: str = "tau"
    reasoning_effort: str | None = None
    reasoning_summary: str = "auto"
    provider_name: str = "OpenAI Codex"


class OpenAICodexProvider:
    """Provider adapter for ChatGPT subscription Codex Responses over SSE."""

    def __init__(
        self,
        config: OpenAICodexConfig,
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
    ) -> AsyncIterator[AssistantMessageEvent]:
        """Stream one response as Pi-compatible assistant message events."""
        raw = self._stream_provider_events(
            model=model, system=system, messages=messages, tools=tools, signal=signal
        )
        return canonicalize_provider_stream(
            raw, api="openai-codex-responses", provider="openai-codex", model=model
        )

    def _stream_provider_events(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Stream one Codex Responses request as provider-neutral events."""

        async def iterator() -> AsyncIterator[ProviderEvent]:
            client = self._get_client()
            payload = _build_codex_payload(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                reasoning_effort=self._config.reasoning_effort,
                reasoning_summary=self._config.reasoning_summary,
            )
            url = _resolve_codex_url(self._config.base_url)

            attempt = 0
            while True:
                emitted_content = False
                try:
                    credentials = await self._config.credential_resolver()
                    headers = _build_codex_headers(
                        self._config.headers,
                        access_token=credentials.access_token,
                        account_id=credentials.account_id,
                        originator=self._config.originator,
                    )
                    async with client.stream(
                        "POST",
                        url,
                        json=payload,
                        headers=headers,
                    ) as response:
                        if response.status_code >= 400:
                            body = await response.aread()
                            body_text = body.decode(errors="replace")
                            if self._should_retry(
                                attempt,
                                status_code=response.status_code,
                                body=body_text,
                            ):
                                delay = retry_delay_seconds(
                                    attempt,
                                    max_delay_seconds=self._config.max_retry_delay_seconds,
                                )
                                yield provider_retry_event(
                                    attempt=attempt,
                                    max_retries=self._config.max_retries,
                                    delay_seconds=delay,
                                    reason=f"HTTP {response.status_code}",
                                    data={
                                        "status_code": response.status_code,
                                        "body": body_text,
                                    },
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
                                data={
                                    "status_code": response.status_code,
                                    "body": body_text,
                                    "attempts": attempt + 1,
                                },
                            )
                            return

                        yield ProviderResponseStartEvent(model=model)
                        async for event in _codex_provider_events(response, signal=signal):
                            if isinstance(
                                event,
                                ProviderTextDeltaEvent | ProviderToolCallEvent,
                            ):
                                emitted_content = True
                            yield event
                        return
                except httpx.HTTPError as exc:
                    if not emitted_content and self._should_retry(attempt):
                        delay = retry_delay_seconds(
                            attempt,
                            max_delay_seconds=self._config.max_retry_delay_seconds,
                        )
                        yield provider_retry_event(
                            attempt=attempt,
                            max_retries=self._config.max_retries,
                            delay_seconds=delay,
                            reason="network error",
                            data={
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            },
                        )
                        attempt += 1
                        if not await wait_for_retry(delay, signal=signal):
                            return
                        continue
                    yield ProviderErrorEvent(
                        message=str(exc),
                        data={"attempts": attempt + 1},
                    )
                    return
                except Exception as exc:  # noqa: BLE001 - provider errors are surfaced as events
                    yield ProviderErrorEvent(message=str(exc), data={"attempts": attempt + 1})
                    return

        return iterator()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = create_async_client(timeout=self._config.timeout_seconds)
        return self._client

    def _should_retry(
        self,
        attempt: int,
        *,
        status_code: int | None = None,
        body: str = "",
    ) -> bool:
        if attempt >= self._config.max_retries:
            return False
        return status_code is None or _is_retryable_status(status_code, body)


class _ToolCallBuilder:
    def __init__(self, *, call_id: str, item_id: str | None, name: str) -> None:
        self.call_id = call_id
        self.item_id = item_id
        self.name = name
        self.arguments_parts: list[str] = []

    def add_delta(self, delta: str) -> None:
        """Append a streamed tool-argument fragment."""
        self.arguments_parts.append(delta)

    def set_arguments(self, arguments: str) -> None:
        """Replace streamed tool arguments with final provider arguments."""
        self.arguments_parts = [arguments]

    def update_from_item(self, item: Mapping[str, Any]) -> None:
        """Fill in metadata from a completed function-call item."""
        call_id = item.get("call_id")
        if isinstance(call_id, str) and call_id:
            self.call_id = call_id
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            self.item_id = item_id
        name = item.get("name")
        if isinstance(name, str):
            self.name = name

    def build(self) -> ToolCall:
        """Build a complete Tau tool call."""
        arguments_text = "".join(self.arguments_parts)
        arguments = _loads_object(arguments_text) if arguments_text else {}
        if arguments is None:
            arguments = {"_raw_arguments": arguments_text}
        item_id = self.item_id or f"fc_{self.call_id}"
        return ToolCall(
            id=f"{self.call_id}|{item_id}",
            name=self.name,
            arguments=arguments,
        )


def _build_codex_payload(
    *,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    reasoning_effort: str | None = None,
    reasoning_summary: str = "auto",
) -> dict[str, JSONValue]:
    payload: dict[str, JSONValue] = {
        "model": model,
        "store": False,
        "stream": True,
        "instructions": system or "You are a helpful assistant.",
        "input": _messages_to_responses_input(messages),
        "text": {"verbosity": "low"},
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }
    if reasoning_effort is not None:
        payload["reasoning"] = {
            "effort": reasoning_effort,
            "summary": reasoning_summary,
        }
    if tools:
        payload["tools"] = [_tool_to_codex(tool) for tool in tools]
    return payload


def _messages_to_responses_input(messages: list[AgentMessage]) -> list[JSONValue]:
    items: list[JSONValue] = []
    assistant_index = 0
    for message in messages:
        if isinstance(message, UserMessage):
            items.append(
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": message.text}],
                }
            )
        elif isinstance(message, AssistantMessage):
            if message.text:
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": message.text,
                                "annotations": [],
                            }
                        ],
                        "status": "completed",
                        "id": f"msg_{assistant_index}",
                    }
                )
                assistant_index += 1
            for tool_call in message.tool_calls:
                call_id, item_id = _split_tool_call_id(tool_call.id)
                item: dict[str, JSONValue] = {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": tool_call.name,
                    "arguments": dumps(tool_call.arguments),
                }
                if item_id:
                    item["id"] = item_id
                items.append(item)
        elif isinstance(message, ToolResultMessage):
            call_id, _item_id = _split_tool_call_id(message.tool_call_id)
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": message.text,
                }
            )
    return items


def _tool_to_codex(tool: AgentTool) -> dict[str, JSONValue]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": dict(tool.input_schema),
        "strict": None,
    }


async def _codex_provider_events(
    response: httpx.Response,
    *,
    signal: CancellationToken | None,
) -> AsyncIterator[ProviderEvent]:
    content_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    active_tools: list[_ToolCallBuilder] = []
    tools_by_item_id: dict[str, _ToolCallBuilder] = {}
    tools_by_call_id: dict[str, _ToolCallBuilder] = {}
    tools_by_output_index: dict[int, _ToolCallBuilder] = {}
    finish_reason: str | None = None
    usage: Usage | None = None

    async for event in _iter_sse_objects(response):
        if signal is not None and signal.is_cancelled():
            return
        event_type = event.get("type")
        if not isinstance(event_type, str):
            continue

        if event_type == "error":
            yield ProviderErrorEvent(
                message=_error_message(event, fallback="OpenAI Codex returned an error"),
                data={"event": event},
            )
            return

        if event_type == "response.failed":
            yield ProviderErrorEvent(
                message=_response_error_message(event),
                data={"event": event},
            )
            return

        if event_type == "response.output_item.added":
            item = event.get("item")
            if isinstance(item, Mapping) and item.get("type") == "function_call":
                _track_tool_builder(
                    _tool_builder_from_item(item),
                    event,
                    active_tools=active_tools,
                    by_item_id=tools_by_item_id,
                    by_call_id=tools_by_call_id,
                    by_output_index=tools_by_output_index,
                )

        elif event_type == "response.function_call_arguments.delta":
            delta = event.get("delta")
            tool_builder = _tool_builder_for_event(
                event,
                active_tools=active_tools,
                by_item_id=tools_by_item_id,
                by_call_id=tools_by_call_id,
                by_output_index=tools_by_output_index,
            )
            if tool_builder is not None and isinstance(delta, str):
                tool_builder.add_delta(delta)

        elif event_type == "response.function_call_arguments.done":
            arguments = event.get("arguments")
            tool_builder = _tool_builder_for_event(
                event,
                active_tools=active_tools,
                by_item_id=tools_by_item_id,
                by_call_id=tools_by_call_id,
                by_output_index=tools_by_output_index,
            )
            if tool_builder is not None and isinstance(arguments, str):
                tool_builder.set_arguments(arguments)

        elif event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                content_parts.append(delta)
                yield ProviderTextDeltaEvent(delta=delta)

        elif event_type in {
            "response.reasoning.delta",
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        }:
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                yield ProviderThinkingDeltaEvent(delta=delta)

        elif event_type in {
            "response.output_item.done",
            "response.output_item.completed",
        }:
            item = event.get("item")
            if isinstance(item, Mapping) and item.get("type") == "function_call":
                tool_builder = _tool_builder_for_event(
                    event,
                    active_tools=active_tools,
                    by_item_id=tools_by_item_id,
                    by_call_id=tools_by_call_id,
                    by_output_index=tools_by_output_index,
                )
                if tool_builder is None:
                    tool_builder = _tool_builder_from_item(item)
                    _track_tool_builder(
                        tool_builder,
                        event,
                        active_tools=active_tools,
                        by_item_id=tools_by_item_id,
                        by_call_id=tools_by_call_id,
                        by_output_index=tools_by_output_index,
                    )
                else:
                    tool_builder.update_from_item(item)
                arguments = item.get("arguments")
                if isinstance(arguments, str):
                    tool_builder.set_arguments(arguments)
                tool_call = tool_builder.build()
                tool_calls.append(tool_call)
                _untrack_tool_builder(
                    tool_builder,
                    active_tools=active_tools,
                    by_item_id=tools_by_item_id,
                    by_call_id=tools_by_call_id,
                    by_output_index=tools_by_output_index,
                )
                yield ProviderToolCallEvent(tool_call=tool_call)
            elif isinstance(item, Mapping) and item.get("type") == "message" and not content_parts:
                text = _text_from_done_message(item)
                if text:
                    content_parts.append(text)
                    yield ProviderTextDeltaEvent(delta=text)

        elif event_type in {
            "response.done",
            "response.completed",
            "response.incomplete",
        }:
            finish_reason = _finish_reason_from_response(event)
            usage = _usage_from_response(event) or usage
            break

    yield ProviderResponseEndEvent(
        message=AssistantMessage(
            content=assistant_content("".join(content_parts), tool_calls),
            usage=usage or Usage(),
        ),
        finish_reason=finish_reason,
    )


async def _iter_sse_objects(response: httpx.Response) -> AsyncIterator[dict[str, JSONValue]]:
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        stripped = line.strip()
        if not stripped:
            if data_lines:
                data = "\n".join(data_lines).strip()
                data_lines = []
                parsed = _loads_object(data)
                if parsed is not None:
                    yield parsed
            continue
        if not stripped.startswith("data:"):
            continue
        value = stripped.removeprefix("data:").strip()
        if value == "[DONE]":
            break
        data_lines.append(value)

    if data_lines:
        parsed = _loads_object("\n".join(data_lines).strip())
        if parsed is not None:
            yield parsed


def _tool_builder_from_item(item: Mapping[str, Any]) -> _ToolCallBuilder:
    call_id = item.get("call_id")
    name = item.get("name")
    item_id = item.get("id")
    return _ToolCallBuilder(
        call_id=call_id if isinstance(call_id, str) and call_id else "call_0",
        item_id=item_id if isinstance(item_id, str) and item_id else None,
        name=name if isinstance(name, str) else "",
    )


def _track_tool_builder(
    builder: _ToolCallBuilder,
    event: Mapping[str, Any],
    *,
    active_tools: list[_ToolCallBuilder],
    by_item_id: dict[str, _ToolCallBuilder],
    by_call_id: dict[str, _ToolCallBuilder],
    by_output_index: dict[int, _ToolCallBuilder],
) -> None:
    if builder not in active_tools:
        active_tools.append(builder)
    if builder.item_id:
        by_item_id[builder.item_id] = builder
    if builder.call_id:
        by_call_id[builder.call_id] = builder
    output_index = _event_output_index(event)
    if output_index is not None:
        by_output_index[output_index] = builder


def _untrack_tool_builder(
    builder: _ToolCallBuilder,
    *,
    active_tools: list[_ToolCallBuilder],
    by_item_id: dict[str, _ToolCallBuilder],
    by_call_id: dict[str, _ToolCallBuilder],
    by_output_index: dict[int, _ToolCallBuilder],
) -> None:
    if builder in active_tools:
        active_tools.remove(builder)
    if builder.item_id and by_item_id.get(builder.item_id) is builder:
        del by_item_id[builder.item_id]
    if builder.call_id and by_call_id.get(builder.call_id) is builder:
        del by_call_id[builder.call_id]
    for output_index, tracked_builder in tuple(by_output_index.items()):
        if tracked_builder is builder:
            del by_output_index[output_index]


def _tool_builder_for_event(
    event: Mapping[str, Any],
    *,
    active_tools: list[_ToolCallBuilder],
    by_item_id: dict[str, _ToolCallBuilder],
    by_call_id: dict[str, _ToolCallBuilder],
    by_output_index: dict[int, _ToolCallBuilder],
) -> _ToolCallBuilder | None:
    item_id = _event_item_id(event)
    if item_id is not None and item_id in by_item_id:
        return by_item_id[item_id]
    call_id = _event_call_id(event)
    if call_id is not None and call_id in by_call_id:
        return by_call_id[call_id]
    output_index = _event_output_index(event)
    if output_index is not None and output_index in by_output_index:
        return by_output_index[output_index]
    if len(active_tools) == 1:
        return active_tools[0]
    return None


def _event_item_id(event: Mapping[str, Any]) -> str | None:
    item_id = event.get("item_id")
    if isinstance(item_id, str) and item_id:
        return item_id
    item = event.get("item")
    if isinstance(item, Mapping):
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id:
            return item_id
    return None


def _event_call_id(event: Mapping[str, Any]) -> str | None:
    call_id = event.get("call_id")
    if isinstance(call_id, str) and call_id:
        return call_id
    item = event.get("item")
    if isinstance(item, Mapping):
        call_id = item.get("call_id")
        if isinstance(call_id, str) and call_id:
            return call_id
    return None


def _event_output_index(event: Mapping[str, Any]) -> int | None:
    output_index = event.get("output_index")
    if isinstance(output_index, int) and not isinstance(output_index, bool):
        return output_index
    return None


def _text_from_done_message(item: Mapping[str, Any]) -> str:
    content = item.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        if part.get("type") == "output_text":
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif part.get("type") == "refusal":
            refusal = part.get("refusal")
            if isinstance(refusal, str):
                parts.append(refusal)
    return "".join(parts)


def _finish_reason_from_response(event: Mapping[str, Any]) -> str | None:
    response = event.get("response")
    if not isinstance(response, Mapping):
        return None
    status = response.get("status")
    if isinstance(status, str):
        return status
    return None


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _usage_from_response(event: Mapping[str, Any]) -> Usage | None:
    """Parse billed usage from a Responses ``response.completed``-style event.

    Ports Pi's openai-responses-shared.ts usage handling: ``cached_tokens`` are
    cache reads and are subtracted from ``input_tokens`` to leave fresh input.
    The Responses API does not report cache writes, so ``cache_write`` stays 0.
    Cost is left unset (None) because Tau has no per-model pricing table.
    """
    response = event.get("response")
    if not isinstance(response, Mapping):
        return None
    raw = response.get("usage")
    if not isinstance(raw, Mapping):
        return None
    input_details = raw.get("input_tokens_details")
    cache_read = (
        _int_or_zero(input_details.get("cached_tokens"))
        if isinstance(input_details, Mapping)
        else 0
    )
    output_details = raw.get("output_tokens_details")
    # Leave reasoning None (not 0) when the provider reports no breakdown,
    # honoring the "None = not reported" contract on Usage.
    reasoning = (
        _int_or_zero(output_details.get("reasoning_tokens"))
        if isinstance(output_details, Mapping)
        else None
    )
    return Usage(
        input=max(0, _int_or_zero(raw.get("input_tokens")) - cache_read),
        output=_int_or_zero(raw.get("output_tokens")),
        cache_read=cache_read,
        cache_write=0,
        reasoning=reasoning,
        total_tokens=_int_or_zero(raw.get("total_tokens")),
    )


def _response_error_message(event: Mapping[str, Any]) -> str:
    response = event.get("response")
    if isinstance(response, Mapping):
        error = response.get("error")
        if isinstance(error, Mapping):
            message = error.get("message")
            code = error.get("code")
            if isinstance(message, str) and message:
                return message
            if isinstance(code, str) and code:
                return f"OpenAI Codex response failed: {code}"
    return "OpenAI Codex response failed"


def _error_message(event: Mapping[str, Any], *, fallback: str) -> str:
    message = event.get("message")
    if isinstance(message, str) and message:
        return message
    code = event.get("code")
    if isinstance(code, str) and code:
        return code
    return fallback


def _build_codex_headers(
    configured_headers: Mapping[str, str] | None,
    *,
    access_token: str,
    account_id: str,
    originator: str,
) -> dict[str, str]:
    headers = {
        **dict(configured_headers or {}),
        "Authorization": f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "originator": originator,
        "User-Agent": f"tau ({system()} {release()}; {machine()})",
        "OpenAI-Beta": "responses=experimental",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }
    return headers


def _resolve_codex_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/codex/responses"):
        return normalized
    if normalized.endswith("/codex"):
        return f"{normalized}/responses"
    return f"{normalized}/codex/responses"


def _split_tool_call_id(value: str) -> tuple[str, str | None]:
    if "|" not in value:
        return value, None
    call_id, item_id = value.split("|", 1)
    return call_id, item_id or None


def _loads_object(value: str) -> dict[str, JSONValue] | None:
    try:
        loaded = loads(value)
    except JSONDecodeError:
        return None
    if isinstance(loaded, dict):
        return loaded
    return None


def _is_retryable_status(status_code: int, body: str) -> bool:
    if status_code == 429 and _is_terminal_rate_limit(body):
        return False
    return status_code in {408, 409, 425, 429} or status_code >= 500


def _is_terminal_rate_limit(body: str) -> bool:
    normalized = body.lower()
    markers = (
        "gousagelimiterror",
        "freeusagelimiterror",
        "monthly usage limit reached",
        "available balance",
        "insufficient_quota",
        "out of budget",
        "quota exceeded",
        "billing",
    )
    return any(marker in normalized for marker in markers)
