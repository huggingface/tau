"""OpenAI-compatible chat completions provider.

Most OpenAI-compatible models are served over `/chat/completions`. Newer
reasoning models (e.g. ``gpt-5.5``/``gpt-5.4`` and the ``*-codex`` family)
reject the combination of function tools and ``reasoning_effort`` on that
endpoint and require ``/v1/responses`` instead. This adapter routes those
models to the Responses API at request time while leaving every other model on
the original chat-completions path unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from json import JSONDecodeError, dumps, loads
from typing import Any, Protocol

import httpx

from tau_agent.messages import (
    AgentMessage,
    AssistantMessage,
    ThinkingContent,
    ToolResultMessage,
    Usage,
    UserMessage,
    assistant_content,
    message_to_user,
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
from tau_ai.env import OpenAICompatibleConfig
from tau_ai.events import AssistantMessageEvent
from tau_ai.http import create_async_client
from tau_ai.http_errors import provider_http_error_message
from tau_ai.provider import CancellationToken
from tau_ai.retry import provider_retry_event, retry_delay_seconds, wait_for_retry
from tau_ai.stream import canonicalize_provider_stream

# Models that reject function tools + reasoning_effort on /chat/completions and
# must use the /v1/responses endpoint instead.
_RESPONSES_ONLY_PREFIXES: tuple[str, ...] = ("gpt-5.5", "gpt-5.4")


def _use_responses_api(model: str) -> bool:
    """Return whether ``model`` must be served over the Responses API."""
    normalized = model.strip().lower()
    if "codex" in normalized:
        return True
    return any(normalized.startswith(prefix) for prefix in _RESPONSES_ONLY_PREFIXES)


class OpenAICompatibleProvider:
    """Provider adapter for OpenAI-compatible `/chat/completions` APIs.

    Models that require it are transparently served over `/v1/responses`.
    """

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
    ) -> AsyncIterator[AssistantMessageEvent]:
        """Stream one response as Pi-compatible assistant message events."""
        raw = self._stream_provider_events(
            model=model, system=system, messages=messages, tools=tools, signal=signal
        )
        return canonicalize_provider_stream(
            raw,
            api=self._config.api,
            provider=getattr(self._config, "provider_name", "openai-compatible"),
            model=model,
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
        """Stream one model response as provider-neutral events."""
        if self._config.api == "openai-responses" or _use_responses_api(model):
            return self._stream_responses(
                model=model,
                system=system,
                messages=messages,
                tools=tools,
                signal=signal,
            )
        return self._stream_chat_completions(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            signal=signal,
        )

    def _stream_chat_completions(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Stream one chat completion response as provider-neutral events."""
        payload = _build_chat_payload(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            reasoning_effort=self._config.reasoning_effort,
            reasoning_effort_parameter=self._config.reasoning_effort_parameter,
            thinking_format=self._config.thinking_format,
            compat=self._config.compat,
            max_tokens=self._config.max_tokens,
            include_reasoning_effort_none=self._config.include_reasoning_effort_none,
        )
        return self._stream(
            model=model,
            url=f"{self._config.base_url.rstrip('/')}/chat/completions",
            payload=payload,
            parser_factory=_ChatStreamParser,
            signal=signal,
        )

    def _stream_responses(
        self,
        *,
        model: str,
        system: str,
        messages: list[AgentMessage],
        tools: list[AgentTool],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Stream one `/v1/responses` response as provider-neutral events."""
        payload = _build_responses_payload(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            reasoning_effort=self._config.reasoning_effort,
            max_tokens=self._config.max_tokens,
        )
        return self._stream(
            model=model,
            url=f"{self._config.base_url.rstrip('/')}/responses",
            payload=payload,
            parser_factory=_ResponsesStreamParser,
            signal=signal,
        )

    def _stream(
        self,
        *,
        model: str,
        url: str,
        payload: Mapping[str, JSONValue],
        parser_factory: Callable[[], _StreamParser],
        signal: CancellationToken | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        """Run the shared streaming POST + retry envelope for a given endpoint.

        The per-endpoint differences (SSE chunk handling and final-message
        assembly) live in the ``_StreamParser`` produced by ``parser_factory``;
        everything else — HTTP, status/network retries, cancellation, the
        opening ``response_start`` event — is identical across endpoints.
        """

        async def iterator() -> AsyncIterator[ProviderEvent]:
            client = self._get_client()
            api_key = self._config.api_key
            request_url = url
            headers = dict(self._config.headers or {})
            if self._config.credential_resolver is not None:
                auth = await self._config.credential_resolver()
                api_key = auth.api_key
                headers.update(auth.headers or {})
                if auth.base_url is not None:
                    endpoint = (
                        "/responses"
                        if url.rstrip("/").endswith("/responses")
                        else "/chat/completions"
                    )
                    request_url = f"{auth.base_url.rstrip('/')}{endpoint}"
            if not self._config.omit_authorization_header:
                has_authorization = any(key.casefold() == "authorization" for key in headers)
                if not has_authorization:
                    headers["Authorization"] = f"Bearer {api_key}"

            attempt = 0
            while True:
                parser = parser_factory()
                try:
                    async with client.stream(
                        "POST", request_url, json=payload, headers=headers
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

                        async for line in response.aiter_lines():
                            if signal is not None and signal.is_cancelled():
                                return

                            event = _parse_sse_line(line)
                            if event is None:
                                continue

                            events, stop = parser.feed(event)
                            for parser_event in events:
                                yield parser_event
                            if stop:
                                break

                        if parser.fatal:
                            return
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

        return iterator()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = create_async_client(timeout=self._config.timeout_seconds)
        return self._client

    def _should_retry(self, attempt: int, *, status_code: int | None = None) -> bool:
        if attempt >= self._config.max_retries:
            return False
        return status_code is None or _is_transient_status(status_code)


class _StreamParser(Protocol):
    """Per-endpoint SSE handler driven by the shared streaming envelope."""

    # True once any model output (text/thinking/tool args) has been emitted;
    # the envelope uses it to decide whether a mid-stream drop is retryable.
    emitted_content: bool
    # True when the parser already emitted a terminal error event and the
    # envelope must not call finalize().
    fatal: bool

    def feed(self, event: str) -> tuple[list[ProviderEvent], bool]:
        """Consume one SSE ``data:`` payload, returning (events, should_stop)."""
        ...

    def finalize(self) -> list[ProviderEvent]:
        """Return the trailing tool-call and response-end events."""
        ...


class _ChatStreamParser:
    """Parser for OpenAI `/chat/completions` SSE chunks."""

    def __init__(self) -> None:
        self.emitted_content = False
        self.fatal = False
        self._content_parts: list[str] = []
        self._thinking_parts: list[str] = []
        self._thinking_signature: str | None = None
        self._tool_call_builders: dict[int, _ToolCallBuilder] = {}
        self._finish_reason: str | None = None
        self._usage: Usage | None = None

    def feed(self, event: str) -> tuple[list[ProviderEvent], bool]:
        if event == "[DONE]":
            return [], True

        chunk = _loads_object(event)
        if chunk is None:
            self.fatal = True
            return [ProviderErrorEvent(message="Provider returned invalid JSON chunk")], True

        # The final usage chunk (from stream_options) carries usage at the top
        # level and often has empty choices.
        chunk_usage = chunk.get("usage")
        if isinstance(chunk_usage, Mapping):
            self._usage = _parse_chunk_usage(chunk_usage)

        choice = _first_choice(chunk)
        if choice is None:
            return [], False

        # Fallback: some providers (e.g. Moonshot) attach usage to the choice
        # instead of the chunk. Matches Pi's per-chunk `!chunk.usage` guard: the
        # fallback applies whenever this chunk lacks top-level usage.
        choice_usage = choice.get("usage")
        if not isinstance(chunk_usage, Mapping) and isinstance(choice_usage, Mapping):
            self._usage = _parse_chunk_usage(choice_usage)

        self._finish_reason = choice.get("finish_reason") or self._finish_reason
        delta = choice.get("delta")
        if not isinstance(delta, Mapping):
            return [], False

        events: list[ProviderEvent] = []
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.emitted_content = True
            self._content_parts.append(content)
            events.append(ProviderTextDeltaEvent(delta=content))

        thinking = _thinking_delta(delta)
        if thinking is not None:
            field_name, text = thinking
            self.emitted_content = True
            self._thinking_parts.append(text)
            self._thinking_signature = self._thinking_signature or field_name
            events.append(ProviderThinkingDeltaEvent(delta=text))

        for tool_call_delta in _tool_call_deltas(delta):
            self.emitted_content = True
            index = int(tool_call_delta.get("index", 0))
            builder = self._tool_call_builders.setdefault(index, _ToolCallBuilder())
            builder.add_delta(tool_call_delta)

        return events, False

    def finalize(self) -> list[ProviderEvent]:
        tool_calls = [
            builder.build(index) for index, builder in sorted(self._tool_call_builders.items())
        ]
        events: list[ProviderEvent] = [
            ProviderToolCallEvent(tool_call=tool_call) for tool_call in tool_calls
        ]
        content = assistant_content("".join(self._content_parts), tool_calls)
        if self._thinking_parts:
            content.insert(
                0,
                ThinkingContent(
                    thinking="".join(self._thinking_parts),
                    thinking_signature=self._thinking_signature,
                ),
            )
        events.append(
            ProviderResponseEndEvent(
                message=AssistantMessage(
                    content=content,
                    usage=self._usage or Usage(),
                ),
                finish_reason=self._finish_reason,
            )
        )
        return events


class _ResponsesStreamParser:
    """Parser for OpenAI `/v1/responses` SSE events."""

    def __init__(self) -> None:
        self.emitted_content = False
        self.fatal = False
        self._content_parts: list[str] = []
        self._thinking_parts: list[str] = []
        self._reasoning_items: dict[str, dict[str, JSONValue]] = {}
        self._tool_call_builders: dict[str, _ResponsesToolCallBuilder] = {}
        self._status: str | None = None
        self._usage: Usage | None = None

    def feed(self, event: str) -> tuple[list[ProviderEvent], bool]:
        # The Responses API has no [DONE] sentinel; it ends with a terminal
        # event (completed/incomplete/failed) handled below.
        if event == "[DONE]":
            return [], False

        chunk = _loads_object(event)
        if chunk is None:
            return [], False

        chunk_type = chunk.get("type")
        if not isinstance(chunk_type, str):
            return [], False

        if chunk_type in ("response.output_text.delta", "response.refusal.delta"):
            delta = chunk.get("delta")
            if isinstance(delta, str) and delta:
                self.emitted_content = True
                self._content_parts.append(delta)
                return [ProviderTextDeltaEvent(delta=delta)], False

        elif chunk_type in (
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        ):
            delta = chunk.get("delta")
            if isinstance(delta, str) and delta:
                self.emitted_content = True
                self._thinking_parts.append(delta)
                return [ProviderThinkingDeltaEvent(delta=delta)], False

        elif chunk_type == "response.output_item.added":
            item = chunk.get("item")
            _register_reasoning_item(self._reasoning_items, item)
            _register_responses_item(
                self._tool_call_builders,
                item,
                output_index=chunk.get("output_index"),
            )

        elif chunk_type == "response.function_call_arguments.delta":
            item_id = chunk.get("item_id")
            if isinstance(item_id, str):
                builder = self._tool_call_builders.setdefault(item_id, _ResponsesToolCallBuilder())
                builder.add_arguments_delta(chunk.get("delta"))
                self.emitted_content = True

        elif chunk_type == "response.function_call_arguments.done":
            item_id = chunk.get("item_id")
            if isinstance(item_id, str):
                builder = self._tool_call_builders.setdefault(item_id, _ResponsesToolCallBuilder())
                builder.set_final(arguments=chunk.get("arguments"))

        elif chunk_type == "response.output_item.done":
            item = chunk.get("item")
            _register_reasoning_item(self._reasoning_items, item)
            _finalize_responses_item(
                self._tool_call_builders,
                item,
                output_index=chunk.get("output_index"),
            )

        elif chunk_type in ("response.completed", "response.incomplete"):
            self._status = _responses_finish_reason(chunk)
            self._usage = _usage_from_responses_event(chunk) or self._usage
            return [], True

        elif chunk_type == "response.failed":
            self.fatal = True
            return [_responses_failure_event(chunk)], True

        elif chunk_type == "error":
            self.fatal = True
            return [
                ProviderErrorEvent(message=_responses_error_message(chunk), data={"event": chunk})
            ], True

        return [], False

    def finalize(self) -> list[ProviderEvent]:
        tool_calls = [
            builder.build(index)
            for index, builder in enumerate(_ordered_builders(self._tool_call_builders))
        ]
        events: list[ProviderEvent] = [
            ProviderToolCallEvent(tool_call=tool_call) for tool_call in tool_calls
        ]
        finish_reason = _normalize_finish_reason(self._status, has_tool_calls=bool(tool_calls))
        content = assistant_content("".join(self._content_parts), tool_calls)
        if self._thinking_parts:
            content.insert(
                0,
                ThinkingContent(
                    thinking="".join(self._thinking_parts),
                    thinking_signature=(
                        dumps(next(iter(self._reasoning_items.values())))
                        if self._reasoning_items
                        else None
                    ),
                ),
            )
        events.append(
            ProviderResponseEndEvent(
                message=AssistantMessage(
                    content=content,
                    usage=self._usage or Usage(),
                ),
                finish_reason=finish_reason,
            )
        )
        return events


class _ToolCallBuilder:
    def __init__(self) -> None:
        self.id = ""
        self.name = ""
        self.arguments_parts: list[str] = []

    def add_delta(self, delta: Mapping[str, Any]) -> None:
        call_id = delta.get("id")
        if isinstance(call_id, str):
            self.id = call_id

        function = delta.get("function")
        if not isinstance(function, Mapping):
            return

        name = function.get("name")
        if isinstance(name, str):
            self.name = name

        arguments = function.get("arguments")
        if isinstance(arguments, str):
            self.arguments_parts.append(arguments)

    def build(self, index: int) -> ToolCall:
        arguments_text = "".join(self.arguments_parts)
        arguments = _loads_object(arguments_text) if arguments_text else {}
        if arguments is None:
            arguments = {"_raw_arguments": arguments_text}

        return ToolCall(
            id=self.id or f"tool-call-{index}",
            name=self.name,
            arguments=arguments,
        )


class _ResponsesToolCallBuilder:
    """Accumulates a streamed Responses-API ``function_call`` output item."""

    def __init__(
        self,
        *,
        call_id: str = "",
        name: str = "",
        output_index: int = 0,
    ) -> None:
        self.call_id = call_id
        self.name = name
        self.output_index = output_index
        self.arguments_parts: list[str] = []
        self.arguments_final: str | None = None

    def add_arguments_delta(self, delta: object) -> None:
        if isinstance(delta, str):
            self.arguments_parts.append(delta)

    def set_final(
        self,
        *,
        call_id: str | None = None,
        name: str | None = None,
        arguments: object = None,
        output_index: int | None = None,
    ) -> None:
        if call_id:
            self.call_id = call_id
        if name:
            self.name = name
        if isinstance(arguments, str):
            self.arguments_final = arguments
        if output_index is not None:
            self.output_index = output_index

    def build(self, index: int) -> ToolCall:
        arguments_text = (
            self.arguments_final
            if self.arguments_final is not None
            else "".join(self.arguments_parts)
        )
        arguments = _loads_object(arguments_text) if arguments_text else {}
        if arguments is None:
            arguments = {"_raw_arguments": arguments_text}

        return ToolCall(
            id=self.call_id or f"tool-call-{index}",
            name=self.name,
            arguments=arguments,
        )


def _build_chat_payload(
    *,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    reasoning_effort: str | None = None,
    reasoning_effort_parameter: str = "reasoning_effort",
    thinking_format: str = "openai",
    compat: Mapping[str, JSONValue] | None = None,
    max_tokens: int | None = None,
    include_reasoning_effort_none: bool = False,
) -> dict[str, JSONValue]:
    resolved_compat = dict(compat or {})
    supports_store = bool(resolved_compat.get("supportsStore", True))
    supports_usage = bool(resolved_compat.get("supportsUsageInStreaming", True))
    supports_reasoning_effort = bool(resolved_compat.get("supportsReasoningEffort", True))
    max_tokens_field = _string_compat(
        resolved_compat.get("maxTokensField"), default="max_completion_tokens"
    )
    payload: dict[str, JSONValue] = {
        "model": model,
        "stream": True,
        "messages": [
            _system_message(system),
            *[_message_to_openai(message) for message in messages],
        ],
    }
    if supports_usage:
        payload["stream_options"] = {"include_usage": True}
    if supports_store:
        payload["store"] = False
    if max_tokens is not None:
        payload["max_tokens" if max_tokens_field == "max_tokens" else "max_completion_tokens"] = (
            max_tokens
        )
    openrouter_provider = resolved_compat.get("openrouterProvider")
    if isinstance(openrouter_provider, dict):
        payload["provider"] = openrouter_provider
    _apply_chat_reasoning(
        payload,
        reasoning_effort=reasoning_effort if supports_reasoning_effort else None,
        reasoning_effort_parameter=reasoning_effort_parameter,
        thinking_format=thinking_format,
        include_reasoning_effort_none=include_reasoning_effort_none,
    )
    if tools:
        payload["tools"] = [_tool_to_openai(tool) for tool in tools]
        if resolved_compat.get("zaiToolStream") is True:
            payload["tool_stream"] = True
    return payload


def _apply_chat_reasoning(
    payload: dict[str, JSONValue],
    *,
    reasoning_effort: str | None,
    reasoning_effort_parameter: str,
    thinking_format: str,
    include_reasoning_effort_none: bool,
) -> None:
    reasoning_enabled = reasoning_effort is not None and reasoning_effort != "none"
    if thinking_format in {"zai", "qwen"}:
        payload["enable_thinking"] = reasoning_enabled
        return
    if thinking_format == "qwen-chat-template":
        payload["chat_template_kwargs"] = {
            "enable_thinking": reasoning_enabled,
            "preserve_thinking": True,
        }
        return
    if thinking_format == "deepseek":
        payload["thinking"] = {"type": "enabled" if reasoning_enabled else "disabled"}
        if reasoning_enabled:
            payload["reasoning_effort"] = reasoning_effort
        return
    if thinking_format == "openrouter" or reasoning_effort_parameter == "reasoning.effort":
        if reasoning_enabled:
            payload["reasoning"] = {"effort": reasoning_effort}
        elif include_reasoning_effort_none:
            payload["reasoning"] = {"effort": "none"}
        return
    if thinking_format == "together":
        payload["reasoning"] = {"enabled": reasoning_enabled}
        if reasoning_enabled:
            payload["reasoning_effort"] = reasoning_effort
        return
    if reasoning_enabled or include_reasoning_effort_none:
        payload["reasoning_effort"] = reasoning_effort or "none"


def _string_compat(value: object, *, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _build_responses_payload(
    *,
    model: str,
    system: str,
    messages: list[AgentMessage],
    tools: list[AgentTool],
    reasoning_effort: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, JSONValue]:
    payload: dict[str, JSONValue] = {
        "model": model,
        "stream": True,
        # Stay stateless: the full transcript is resent every turn, so there is
        # no need for server-side retention. ``store: false`` also keeps the
        # path usable for zero-data-retention orgs, which reject ``store: true``.
        "store": False,
        "instructions": system,
        "input": _messages_to_responses_input(messages),
    }
    if max_tokens is not None:
        payload["max_output_tokens"] = max_tokens
    effort = _normalize_responses_effort(reasoning_effort)
    if effort is not None:
        # ``summary: auto`` streams ``response.reasoning_summary_text.delta``
        # events so the agent's thinking is visible, mirroring the reasoning
        # deltas surfaced on the chat-completions path.
        payload["reasoning"] = {"effort": effort, "summary": "auto"}
    if tools:
        payload["tools"] = [_tool_to_responses(tool) for tool in tools]
    return payload


def _normalize_responses_effort(reasoning_effort: str | None) -> str | None:
    """Map an internal reasoning level to a Responses-API effort, or drop it."""
    if reasoning_effort is None:
        return None
    normalized = reasoning_effort.strip().lower()
    if normalized in ("", "none"):
        return None
    return normalized


def _messages_to_responses_input(
    messages: list[AgentMessage],
) -> list[JSONValue]:
    items: list[JSONValue] = []
    for message in messages:
        if isinstance(message, UserMessage):
            items.append({"role": "user", "content": message.text})
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ThinkingContent) and block.thinking_signature:
                    try:
                        reasoning_item = loads(block.thinking_signature)
                    except (TypeError, ValueError):
                        reasoning_item = None
                    if isinstance(reasoning_item, dict):
                        items.append(reasoning_item)
            if message.text:
                items.append({"role": "assistant", "content": message.text})
            for tool_call in message.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": dumps(tool_call.arguments),
                    }
                )
        elif isinstance(message, ToolResultMessage):
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.text,
                }
            )
    return items


def _tool_to_responses(tool: AgentTool) -> dict[str, JSONValue]:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": dict(tool.input_schema),
    }


def _register_reasoning_item(
    items: dict[str, dict[str, JSONValue]],
    item: object,
) -> None:
    if not isinstance(item, Mapping) or item.get("type") != "reasoning":
        return
    item_id = item.get("id")
    if isinstance(item_id, str):
        items[item_id] = dict(item)


def _register_responses_item(
    builders: dict[str, _ResponsesToolCallBuilder],
    item: object,
    *,
    output_index: object,
) -> None:
    if not isinstance(item, Mapping) or item.get("type") != "function_call":
        return
    item_id = item.get("id")
    if not isinstance(item_id, str):
        return
    raw_arguments = item.get("arguments")
    builder = builders.setdefault(item_id, _ResponsesToolCallBuilder())
    builder.set_final(
        call_id=_str_or_none(item.get("call_id")),
        name=_str_or_none(item.get("name")),
        arguments=raw_arguments if isinstance(raw_arguments, str) and raw_arguments else None,
        output_index=_int_or_none(output_index),
    )


def _finalize_responses_item(
    builders: dict[str, _ResponsesToolCallBuilder],
    item: object,
    *,
    output_index: object,
) -> None:
    if not isinstance(item, Mapping) or item.get("type") != "function_call":
        return
    item_id = item.get("id")
    if not isinstance(item_id, str):
        return
    builder = builders.setdefault(item_id, _ResponsesToolCallBuilder())
    builder.set_final(
        call_id=_str_or_none(item.get("call_id")),
        name=_str_or_none(item.get("name")),
        arguments=item.get("arguments"),
        output_index=_int_or_none(output_index),
    )


def _ordered_builders(
    builders: dict[str, _ResponsesToolCallBuilder],
) -> list[_ResponsesToolCallBuilder]:
    return [
        builder for _, builder in sorted(builders.items(), key=lambda pair: pair[1].output_index)
    ]


def _responses_finish_reason(chunk: Mapping[str, Any]) -> str | None:
    response = chunk.get("response")
    if isinstance(response, Mapping):
        status = response.get("status")
        if isinstance(status, str):
            return status
    return None


def _normalize_finish_reason(status: str | None, *, has_tool_calls: bool) -> str:
    """Map a Responses-API status to chat-completions-style finish reasons."""
    if has_tool_calls:
        return "tool_calls"
    if status == "incomplete":
        return "length"
    return "stop"


def _responses_failure_event(chunk: Mapping[str, Any]) -> ProviderErrorEvent:
    message = "Provider response failed"
    response = chunk.get("response")
    if isinstance(response, Mapping):
        error = response.get("error")
        if isinstance(error, Mapping):
            error_message = error.get("message")
            if isinstance(error_message, str) and error_message:
                message = error_message
    return ProviderErrorEvent(message=message, data={"event": dict(chunk)})


def _responses_error_message(chunk: Mapping[str, Any]) -> str:
    message = chunk.get("message")
    if isinstance(message, str) and message:
        return message
    error = chunk.get("error")
    if isinstance(error, Mapping):
        nested = error.get("message")
        if isinstance(nested, str) and nested:
            return nested
    return "Provider stream error"


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _system_message(system: str) -> dict[str, JSONValue]:
    return {"role": "system", "content": system}


def _message_to_openai(message: AgentMessage) -> dict[str, JSONValue]:
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.text}

    if isinstance(message, AssistantMessage):
        item: dict[str, JSONValue] = {"role": "assistant", "content": message.text}
        thinking = [block for block in message.content if isinstance(block, ThinkingContent)]
        if thinking:
            signature = thinking[0].thinking_signature or "reasoning_content"
            if signature in {"reasoning_content", "reasoning", "thinking"}:
                item[signature] = "".join(block.thinking for block in thinking)
        if message.tool_calls:
            item["tool_calls"] = [
                _tool_call_to_openai(tool_call) for tool_call in message.tool_calls
            ]
        return item

    if isinstance(message, ToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "name": message.tool_name,
            "content": message.text,
        }
    return _message_to_openai(message_to_user(message))


def _tool_to_openai(tool: AgentTool) -> dict[str, JSONValue]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        },
    }


def _tool_call_to_openai(tool_call: ToolCall) -> dict[str, JSONValue]:
    return {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.name,
            "arguments": dumps(tool_call.arguments),
        },
    }


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
    if isinstance(loaded, dict):
        return loaded
    return None


def _first_choice(chunk: Mapping[str, Any]) -> Mapping[str, Any] | None:
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, Mapping):
        return None
    return choice


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_chunk_usage(raw: Mapping[str, Any]) -> Usage:
    """Parse an OpenAI-compatible ``usage`` payload into a Usage.

    Ports Pi's openai-completions.ts parseChunkUsage: ``cached_tokens`` are
    cache reads, writes are subtracted from the prompt to leave the fresh input,
    and ``completion_tokens`` already includes reasoning tokens. Cost is left
    unset (None) because Tau has no per-model pricing table.
    """
    prompt_tokens = _int_or_zero(raw.get("prompt_tokens"))
    prompt_details = raw.get("prompt_tokens_details")
    cached_tokens: int | None = None
    cache_write = 0
    if isinstance(prompt_details, Mapping):
        cached_tokens = _int_or_none(prompt_details.get("cached_tokens"))
        cache_write = _int_or_zero(prompt_details.get("cache_write_tokens"))
    # Nullish fallback, matching Pi's `cached_tokens ?? prompt_cache_hit_tokens
    # ?? 0` (DeepSeek reports cache hits in prompt_cache_hit_tokens): a reported
    # 0 does not fall through.
    if cached_tokens is None:
        cached_tokens = _int_or_none(raw.get("prompt_cache_hit_tokens"))
    cache_read = cached_tokens or 0
    fresh_input = max(0, prompt_tokens - cache_read - cache_write)
    output = _int_or_zero(raw.get("completion_tokens"))
    reasoning = None
    completion_details = raw.get("completion_tokens_details")
    if isinstance(completion_details, Mapping):
        reasoning = _int_or_zero(completion_details.get("reasoning_tokens"))
    return Usage(
        input=fresh_input,
        output=output,
        cache_read=cache_read,
        cache_write=cache_write,
        reasoning=reasoning,
        total_tokens=fresh_input + output + cache_read + cache_write,
    )


def _usage_from_responses_event(chunk: Mapping[str, Any]) -> Usage | None:
    """Parse billed usage from a `/v1/responses` terminal event.

    Mirrors the Codex adapter's ``_usage_from_response``: ``cached_tokens`` are
    cache reads subtracted from ``input_tokens`` to leave fresh input, the
    Responses API does not report cache writes (``cache_write`` stays 0), and
    cost is left unset because Tau has no per-model pricing table.
    """
    response = chunk.get("response")
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


def _tool_call_deltas(delta: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tool_calls = delta.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []
    return [tool_call for tool_call in tool_calls if isinstance(tool_call, Mapping)]


def _thinking_delta(delta: Mapping[str, Any]) -> tuple[str, str] | None:
    for field_name in ("reasoning_content", "reasoning", "thinking"):
        value = delta.get(field_name)
        if isinstance(value, str) and value:
            return field_name, value
    return None


def _is_transient_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429} or status_code >= 500
