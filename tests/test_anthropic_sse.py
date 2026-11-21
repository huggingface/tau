import json
from collections.abc import AsyncIterator

import httpx
import pytest

from tau_agent import ThinkingContent, UserMessage
from tau_ai import AnthropicConfig, AnthropicProvider, AssistantDoneEvent


async def _collect(stream: AsyncIterator[object]) -> list[object]:
    return [event async for event in stream]


@pytest.mark.anyio
async def test_anthropic_provider_ignores_empty_data_frames() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"message_start","message":{"usage":{}}}\n\n'
                "data:\n\n"
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"ok"}}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
                '"usage":{"output_tokens":1}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
            ),
            client=client,
        )
        events = await _collect(
            provider.stream_response(
                model="claude-test",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.text == "ok"


def _sse(*events: dict[str, object]) -> str:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


async def _stream_once(body: str, requests: list[httpx.Request] | None = None) -> list[object]:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )
        return await _collect(
            provider.stream_response(
                model="claude-test",
                system="You are Tau.",
                messages=[UserMessage(content="Go")],
                tools=[],
            )
        )


@pytest.mark.anyio
async def test_anthropic_provider_keeps_signature_per_thinking_block() -> None:
    events = await _stream_once(
        _sse(
            {"type": "message_start", "message": {"usage": {}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "first"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "SIG1"},
            },
            {"type": "content_block_start", "index": 1, "content_block": {"type": "thinking"}},
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "thinking_delta", "thinking": "second"},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "signature_delta", "signature": "SIG2"},
            },
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}},
            {"type": "message_stop"},
        )
    )

    assert isinstance(events[-1], AssistantDoneEvent)
    thinking = [b for b in events[-1].message.content if isinstance(b, ThinkingContent)]
    assert [(b.thinking, b.thinking_signature) for b in thinking] == [
        ("first", "SIG1"),
        ("second", "SIG2"),
    ]


@pytest.mark.anyio
async def test_anthropic_provider_keeps_and_replays_redacted_thinking() -> None:
    events = await _stream_once(
        _sse(
            {"type": "message_start", "message": {"usage": {}}},
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "redacted_thinking", "data": "ENC"},
            },
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "ok"},
            },
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}},
            {"type": "message_stop"},
        )
    )

    assert isinstance(events[-1], AssistantDoneEvent)
    message = events[-1].message
    assert message.content[0] == ThinkingContent(
        thinking="", thinking_signature="ENC", redacted=True
    )

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=_sse({"type": "message_stop"}),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )
        await _collect(
            provider.stream_response(
                model="claude-test",
                system="You are Tau.",
                messages=[UserMessage(content="Go"), message, UserMessage(content="More")],
                tools=[],
            )
        )

    assistant = json.loads(requests[0].content)["messages"][1]
    assert assistant["content"][0] == {"type": "redacted_thinking", "data": "ENC"}
