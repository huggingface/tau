"""Anthropic Messages API provider."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from json import loads
from typing import Any, cast

import httpx

from tau_agent.messages import (
    AgentMessage,
    AssistantMessage,
    ImageContent,
    TextContent,
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
from tau_ai.content import (
    NON_VISION_TOOL_IMAGE_PLACEHOLDER,
    NON_VISION_USER_IMAGE_PLACEHOLDER,
    messages_have_images,
    text_and_images,
)
from tau_ai.env import (
    CACHE_RETENTION_LONG,
    CACHE_RETENTION_NONE,
    CACHE_RETENTION_SHORT,
    AnthropicConfig,
    CacheRetention,
)
from tau_ai.events import AssistantMessageEvent
from tau_ai.http import create_async_client
from tau_ai.http_errors import provider_http_error_message
from tau_ai.provider import CancellationToken
from tau_ai.retry import provider_retry_event, retry_delay_seconds, wait_for_retry
from tau_ai.stream import canonicalize_provider_stream
from tau_ai.tool_call_ids import portable_tool_call_id

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
CACHE_TTL_LONG = "1h"

# Anthropic rejects a request carrying more than four cache breakpoints, so the
# budget is spent where it buys the most: the tool schemas, the system prompt, and
# the two most recent request tails. See _apply_message_cache_breakpoints.
MAX_CACHE_BREAKPOINTS = 4
SYSTEM_CACHE_BREAKPOINTS = 1
TOOLS_CACHE_BREAKPOINTS = 1
MESSAGE_CACHE_BREAKPOINTS = (
    MAX_CACHE_BREAKPOINTS - SYSTEM_CACHE_BREAKPOINTS - TOOLS_CACHE_BREAKPOINTS
)

# Block types that may carry cache_control.
CACHEABLE_BLOCK_TYPES = frozenset({"text", "image", "tool_result"})


class AnthropicProvider:
    """Provider adapter for Anthropic's streaming Messages API."""

    def __init__(
        self,
        config: AnthropicConfig,
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
        session_id: str | None = None,
    ) -> AsyncIterator[AssistantMessageEvent]:
        """Stream one response as Pi-compatible assistant message events."""
        del session_id
        raw = self._stream_provider_events(
            model=model, system=system, messages=messages, tools=tools, signal=signal
        )
        return canonicalize_provider_stream(
            raw, api="anthropic-messages", provider="anthropic", model=model
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
        """Stream one Anthropic response as provider-neutral events."""

        async def iterator() -> AsyncIterator[ProviderEvent]:
            client = self._get_client()
            api_key = self._config.api_key
            base_url = self._config.base_url
            auth_headers: dict[str, str] = {}
            if self._config.credential_resolver is not None:
                auth = await self._config.credential_resolver()
                api_key = auth.api_key
                if auth.base_url is not None:
                    base_url = auth.base_url.rstrip("/")
                    if not base_url.endswith("/v1"):
                        base_url = f"{base_url}/v1"
                auth_headers.update(auth.headers or {})
            payload = _build_messages_payload(
                model=model,
                system=system,
                oauth_system_prompt=self._config.oauth_system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=self._config.max_tokens,
                thinking_budget_tokens=self._config.thinking_budget_tokens,
                thinking_effort=self._config.thinking_effort,
                thinking_mode=self._config.thinking_mode,
                supports_images=self._config.supports_images,
                cache_retention=self._config.cache_retention,
                cache_control_on_tools=self._config.cache_control_on_tools,
            )
            headers = {
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
                **(dict(self._config.headers or {})),
                **auth_headers,
            }
            if (
                self._config.provider_name == "github-copilot"
                and self._config.supports_images
                and messages_have_images(messages)
            ):
                headers["Copilot-Vision-Request"] = "true"
            if self._config.bearer_auth:
                headers.setdefault("Authorization", f"Bearer {api_key}")
            else:
                headers["x-api-key"] = api_key
            url = f"{base_url.rstrip('/')}/messages"

            attempt = 0
            while True:
                emitted_content = False
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
                        stream_error: dict[str, JSONValue] | None = None
                        content_parts: list[str] = []
                        thinking_parts: list[str] = []
                        thinking_signature: str | None = None
                        tool_builders: dict[int, _AnthropicToolBuilder] = {}
                        finish_reason: str | None = None
                        usage: Usage | None = None

                        async for line in response.aiter_lines():
                            if signal is not None and signal.is_cancelled():
                                return

                            event = _parse_sse_line(line)
                            if event is None:
                                continue
                            chunk = _loads_object(event)
                            if chunk is None:
                                yield ProviderErrorEvent(
                                    message="Provider returned invalid JSON chunk"
                                )
                                return

                            event_type = chunk.get("type")
                            if event_type == "message_start":
                                message = chunk.get("message")
                                if isinstance(message, Mapping):
                                    usage = _usage_from_message_start(message.get("usage"))
                            elif event_type == "content_block_start":
                                block = chunk.get("content_block")
                                if isinstance(block, Mapping) and block.get("type") == "tool_use":
                                    index = int(chunk.get("index", 0))
                                    builder = tool_builders.setdefault(
                                        index, _AnthropicToolBuilder()
                                    )
                                    builder.id = _string_or_empty(block.get("id"))
                                    builder.name = _string_or_empty(block.get("name"))
                                    emitted_content = True
                            elif event_type == "content_block_delta":
                                delta = chunk.get("delta")
                                if not isinstance(delta, Mapping):
                                    continue
                                delta_type = delta.get("type")
                                if delta_type == "text_delta":
                                    text = _string_or_empty(delta.get("text"))
                                    if text:
                                        emitted_content = True
                                        content_parts.append(text)
                                        yield ProviderTextDeltaEvent(delta=text)
                                elif delta_type == "thinking_delta":
                                    thinking = _string_or_empty(delta.get("thinking"))
                                    if thinking:
                                        emitted_content = True
                                        thinking_parts.append(thinking)
                                        yield ProviderThinkingDeltaEvent(delta=thinking)
                                elif delta_type == "signature_delta":
                                    signature = _string_or_empty(delta.get("signature"))
                                    if signature:
                                        thinking_signature = (
                                            f"{thinking_signature or ''}{signature}"
                                        )
                                elif delta_type == "input_json_delta":
                                    index = int(chunk.get("index", 0))
                                    builder = tool_builders.setdefault(
                                        index, _AnthropicToolBuilder()
                                    )
                                    builder.arguments_parts.append(
                                        _string_or_empty(delta.get("partial_json"))
                                    )
                                    emitted_content = True
                            elif event_type == "message_delta":
                                delta = chunk.get("delta")
                                if isinstance(delta, Mapping):
                                    finish_reason = (
                                        _string_or_empty(delta.get("stop_reason")) or finish_reason
                                    )
                                usage = _apply_message_delta_usage(usage, chunk.get("usage"))
                            elif event_type == "error":
                                error_type, message = _anthropic_stream_error_details(chunk)
                                if (
                                    not emitted_content
                                    and self._should_retry(attempt)
                                    and _retryable_anthropic_stream_error(error_type)
                                ):
                                    stream_error = chunk
                                    break
                                yield ProviderErrorEvent(
                                    message=message,
                                    data={"event": chunk, "attempts": attempt + 1},
                                )
                                return

                        if stream_error is not None:
                            error_type, _message = _anthropic_stream_error_details(stream_error)
                            delay = retry_delay_seconds(
                                attempt,
                                max_delay_seconds=self._config.max_retry_delay_seconds,
                            )
                            yield provider_retry_event(
                                attempt=attempt,
                                max_retries=self._config.max_retries,
                                delay_seconds=delay,
                                reason=f"stream error ({error_type or 'unknown'})",
                                data={"event": stream_error},
                            )
                            attempt += 1
                            if not await wait_for_retry(delay, signal=signal):
                                return
                            continue

                        tool_calls = [
                            builder.build(index) for index, builder in sorted(tool_builders.items())
                        ]
                        for tool_call in tool_calls:
                            yield ProviderToolCallEvent(tool_call=tool_call)

                        content = assistant_content("".join(content_parts), tool_calls)
                        if thinking_parts:
                            content.insert(
                                0,
                                ThinkingContent(
                                    thinking="".join(thinking_parts),
                                    thinking_signature=thinking_signature,
                                ),
                            )
                        yield ProviderResponseEndEvent(
                            message=AssistantMessage(
                                content=content,
                                usage=usage or Usage(),
                            ),
                            finish_reason=finish_reason,
                        )
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

        return iterator()

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = create_async_client(timeout=self._config.timeout_seconds)
        return self._client

    def _should_retry(self, attempt: int, *, status_code: int | None = None) -> bool:
        if attempt >= self._config.max_retries:
            return False
        return status_code is None or status_code in {408, 409, 425, 429} or status_code >= 500


_TRANSIENT_ANTHROPIC_STREAM_ERROR_TYPES = frozenset(
    {
        "api_error",
        "overloaded_error",
        "rate_limit_error",
    }
)


def _anthropic_stream_error_details(event: Mapping[str, JSONValue]) -> tuple[str, str]:
    """Return the provider classification and message from an Anthropic SSE error."""
    error = event.get("error")
    if not isinstance(error, Mapping):
        return "", "Provider returned an error"
    error_type = _string_or_empty(error.get("type"))
    message = _string_or_empty(error.get("message")) or "Provider returned an error"
    return error_type, message


def _retryable_anthropic_stream_error(error_type: str) -> bool:
    """Return whether an Anthropic SSE error is transient and safe to retry."""
    return error_type.lower() in _TRANSIENT_ANTHROPIC_STREAM_ERROR_TYPES


class _AnthropicToolBuilder:
    def __init__(self) -> None:
        self.id = ""
        self.name = ""
        self.arguments_parts: list[str] = []

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


def _build_messages_payload(
    *,
    model: str,
    system: str,
    messages: list[AgentMessage],
    oauth_system_prompt: str | None = None,
    tools: list[AgentTool],
    max_tokens: int | None = None,
    thinking_budget_tokens: int | None = None,
    thinking_effort: str | None = None,
    thinking_mode: str = "budget",
    supports_images: bool = False,
    cache_retention: CacheRetention = CACHE_RETENTION_SHORT,
    cache_control_on_tools: bool = True,
) -> dict[str, JSONValue]:
    resolved_max_tokens = max_tokens or DEFAULT_MAX_TOKENS
    if thinking_budget_tokens is not None:
        resolved_max_tokens = max(resolved_max_tokens, thinking_budget_tokens + 1024)
    cache_control = _cache_control(cache_retention)
    payload_messages = []
    for message in messages:
        converted = _anthropic_message(message, supports_images=supports_images)
        # Dropping foreign provider reasoning can empty a reasoning-only turn.
        # Anthropic rejects empty assistant content, so omit that inert turn too.
        if converted.get("role") == "assistant" and not converted.get("content"):
            continue
        payload_messages.append(converted)
    _apply_message_cache_breakpoints(payload_messages, cache_control)
    payload: dict[str, JSONValue] = {
        "model": model,
        "max_tokens": resolved_max_tokens,
        "stream": True,
        "system": _anthropic_system(system, oauth_system_prompt, cache_control),
        "messages": cast("JSONValue", payload_messages),
    }
    if thinking_mode == "disabled":
        payload["thinking"] = {"type": "disabled"}
    elif thinking_mode == "adaptive" and thinking_effort is not None:
        payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        payload["output_config"] = {"effort": thinking_effort}
    elif thinking_budget_tokens is not None:
        payload["thinking"] = {
            "type": "enabled",
            "budget_tokens": thinking_budget_tokens,
        }
    if tools:
        # Some Anthropic-protocol gateways accept cache_control everywhere except
        # inside tool objects, so the tools breakpoint is separately suppressible.
        tools_cache_control = cache_control if cache_control_on_tools else None
        last_index = len(tools) - 1
        payload["tools"] = [
            _anthropic_tool(
                tool, cache_control=tools_cache_control if index == last_index else None
            )
            for index, tool in enumerate(tools)
        ]
    return payload


def _cache_control(cache_retention: CacheRetention) -> dict[str, JSONValue] | None:
    """Return the cache_control marker for a retention preference, if enabled.

    Attach sites copy the result, so no two breakpoints share one dict.
    """
    if cache_retention == CACHE_RETENTION_NONE:
        return None
    if cache_retention == CACHE_RETENTION_LONG:
        return {"type": "ephemeral", "ttl": CACHE_TTL_LONG}
    return {"type": "ephemeral"}


def _anthropic_system(
    system: str,
    oauth_system_prompt: str | None,
    cache_control: dict[str, JSONValue] | None,
) -> JSONValue:
    """Build the system field, marking its tail as a cache breakpoint when enabled.

    Only the final block is marked. A breakpoint on the OAuth identity block would
    cache a prefix already covered by the block after it, wasting one of the four
    breakpoints Anthropic allows.
    """
    if cache_control is None:
        if oauth_system_prompt:
            return [
                {"type": "text", "text": oauth_system_prompt},
                {"type": "text", "text": system},
            ]
        return system
    blocks: list[dict[str, JSONValue]] = []
    if oauth_system_prompt:
        blocks.append({"type": "text", "text": oauth_system_prompt})
    if system:
        blocks.append({"type": "text", "text": system})
    if not blocks:
        # An empty text block carrying cache_control is rejected outright.
        return system
    blocks[-1]["cache_control"] = dict(cache_control)
    return cast("JSONValue", blocks)


def _apply_message_cache_breakpoints(
    messages: list[dict[str, JSONValue]],
    cache_control: dict[str, JSONValue] | None,
) -> None:
    """Mark this request's tail and the previous request's tail, in place.

    Two breakpoints rather than one: Anthropic checks at most 20 block positions
    back from a breakpoint when searching for a reusable prefix, and one tau turn
    appends 2N+2 blocks for N tool calls. Marking where the previous request ended
    opens a second lookback window there, so a wide parallel-tool turn still gets
    a read hit instead of falling out of the window. Either position may be
    ineligible, in which case fewer breakpoints are emitted.
    """
    if cache_control is None or not messages:
        return
    indexes = {len(messages) - 1}
    boundary = _previous_request_boundary(messages)
    if boundary is not None:
        indexes.add(boundary)
    if len(indexes) > MESSAGE_CACHE_BREAKPOINTS:  # pragma: no cover - defensive
        raise AssertionError("message cache breakpoints exceed the Anthropic budget")
    for index in indexes:
        _mark_cache_breakpoint(messages[index], cache_control)


def _previous_request_boundary(messages: list[dict[str, JSONValue]]) -> int | None:
    """Return the index where the previous request's message list ended.

    Tau's transcript is append-only and every request stops immediately before the
    assistant message it produces, so the last user message preceding the final
    assistant turn is where the previous request's tail breakpoint was placed.

    Two cases return an older position than the literal previous request. A turn
    whose assistant message was empty and errored or aborted is filtered out of
    provider context, and consecutive assistant messages (a retained failure
    followed by a continue) leave no user message at the true boundary. Both only
    shorten the prefix this breakpoint can reuse; a marked position that was never
    written simply opens a lookback window that finds an older entry, and
    breakpoints themselves are not billed.
    """
    last_assistant = None
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("role") == "assistant":
            last_assistant = index
            break
    if last_assistant is None:
        return None
    for index in range(last_assistant - 1, -1, -1):
        if messages[index].get("role") == "user":
            return index
    return None


def _mark_cache_breakpoint(
    message: dict[str, JSONValue],
    cache_control: dict[str, JSONValue],
) -> None:
    """Attach cache_control to a user message's final content block, if eligible."""
    if message.get("role") != "user":
        return
    content = message.get("content")
    if isinstance(content, str):
        if not content:
            return
        message["content"] = [
            {"type": "text", "text": content, "cache_control": dict(cache_control)}
        ]
        return
    if not isinstance(content, list) or not content:
        return
    last_block = content[-1]
    if not isinstance(last_block, dict):
        return
    if last_block.get("type") not in CACHEABLE_BLOCK_TYPES:
        return
    if last_block.get("type") == "tool_result":
        # A breakpoint on a tool_result carrying no content risks the same
        # rejection as an empty text block.
        inner = last_block.get("content")
        if isinstance(inner, list) and not inner:
            return
    last_block["cache_control"] = dict(cache_control)


def _anthropic_message(message: AgentMessage, *, supports_images: bool) -> dict[str, JSONValue]:
    if isinstance(message, UserMessage):
        text, images = text_and_images(
            message.content,
            supports_images=supports_images,
            image_placeholder=NON_VISION_USER_IMAGE_PLACEHOLDER,
        )
        if not images:
            return {"role": "user", "content": text}
        user_content: list[JSONValue] = []
        if text:
            user_content.append({"type": "text", "text": text})
        user_content.extend(_anthropic_image(image) for image in images)
        return {"role": "user", "content": user_content}
    if isinstance(message, AssistantMessage):
        content: list[JSONValue] = []
        for block in message.content:
            if isinstance(block, TextContent):
                content.append({"type": "text", "text": block.text})
            elif isinstance(block, ThinkingContent):
                # Thinking signatures are provider-owned opaque state. Replaying
                # an OpenAI/Google signature as an Anthropic thinking block makes
                # an otherwise portable model switch fail validation.
                if message.api != "anthropic-messages":
                    continue
                thinking: dict[str, JSONValue] = {
                    "type": "thinking",
                    "thinking": block.thinking,
                }
                if block.thinking_signature is not None:
                    thinking["signature"] = block.thinking_signature
                content.append(thinking)
            elif isinstance(block, ToolCall):
                content.append(
                    {
                        "type": "tool_use",
                        "id": portable_tool_call_id(block.id),
                        "name": block.name,
                        "input": block.arguments,
                    }
                )
        return {"role": "assistant", "content": content}
    if isinstance(message, ToolResultMessage):
        text, images = text_and_images(
            message.content,
            supports_images=supports_images,
            image_placeholder=NON_VISION_TOOL_IMAGE_PLACEHOLDER,
        )
        result_content: list[JSONValue] = []
        if text:
            result_content.append({"type": "text", "text": text})
        result_content.extend(_anthropic_image(image) for image in images)
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": portable_tool_call_id(message.tool_call_id),
                    "content": result_content,
                    "is_error": bool(message.is_error),
                }
            ],
        }
    return _anthropic_message(message_to_user(message), supports_images=supports_images)


def _anthropic_image(image: ImageContent) -> dict[str, JSONValue]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image.mime_type,
            "data": image.data,
        },
    }


def _anthropic_tool(
    tool: AgentTool,
    *,
    cache_control: dict[str, JSONValue] | None = None,
) -> dict[str, JSONValue]:
    payload: dict[str, JSONValue] = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": dict(tool.input_schema),
    }
    if cache_control is not None:
        payload["cache_control"] = dict(cache_control)
    return payload


def _parse_sse_line(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    return line.removeprefix("data:").strip()


def _loads_object(text: str) -> dict[str, Any] | None:
    try:
        value = loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _string_or_empty(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage_from_message_start(raw: object) -> Usage:
    """Build a Usage from the ``message_start`` event's ``message.usage``.

    Ports Pi's anthropic-messages.ts message_start handling. Cost is left unset
    (None) because Tau has no per-model pricing table.
    """
    data = raw if isinstance(raw, Mapping) else {}
    cache_creation = data.get("cache_creation")
    cache_write_1h = (
        _int_or_none(cache_creation.get("ephemeral_1h_input_tokens"))
        if isinstance(cache_creation, Mapping)
        else None
    )
    usage = Usage(
        input=_int_or_none(data.get("input_tokens")) or 0,
        output=_int_or_none(data.get("output_tokens")) or 0,
        cache_read=_int_or_none(data.get("cache_read_input_tokens")) or 0,
        cache_write=_int_or_none(data.get("cache_creation_input_tokens")) or 0,
        cache_write_1h=cache_write_1h,
    )
    usage.total_tokens = usage.input + usage.output + usage.cache_read + usage.cache_write
    return usage


def _apply_message_delta_usage(usage: Usage | None, raw: object) -> Usage | None:
    """Apply the ``message_delta`` event's ``usage`` onto the running Usage.

    Ports Pi's anthropic-messages.ts message_delta handling: only overwrite
    fields the provider reports (non-null), then recompute the token total.
    """
    if not isinstance(raw, Mapping):
        return usage
    usage = usage or Usage()
    if (value := _int_or_none(raw.get("input_tokens"))) is not None:
        usage.input = value
    if (value := _int_or_none(raw.get("output_tokens"))) is not None:
        usage.output = value
    if (value := _int_or_none(raw.get("cache_read_input_tokens"))) is not None:
        usage.cache_read = value
    if (value := _int_or_none(raw.get("cache_creation_input_tokens"))) is not None:
        usage.cache_write = value
    details = raw.get("output_tokens_details")
    if isinstance(details, Mapping):
        thinking = _int_or_none(details.get("thinking_tokens"))
        if thinking is not None:
            usage.reasoning = thinking
    usage.total_tokens = usage.input + usage.output + usage.cache_read + usage.cache_write
    return usage
