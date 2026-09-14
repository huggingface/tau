from collections.abc import AsyncIterator

import httpx
import pytest

from tau_agent import UserMessage
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
