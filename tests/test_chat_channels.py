"""Chat Completions fields are independent channels, not ordered blocks."""

import json

import httpx
import pytest

from tau_agent.messages import ThinkingContent
from tau_ai import OpenAICompatibleConfig, OpenAICompatibleProvider
from tau_ai.events import AssistantDoneEvent, TextDeltaEvent


@pytest.mark.anyio
@pytest.mark.parametrize("same_chunk", [False, True])
async def test_interleaved_reasoning_keeps_stable_blocks(same_chunk: bool) -> None:
    deltas = [
        {"reasoning_content": "Done. C"},
        {"content": "Done. Pr"},
        {"reasoning_content": "reated and pushed."},
        {"content": "ivate repo created."},
    ]
    if same_chunk:
        deltas = [deltas[0] | deltas[1], deltas[2] | deltas[3]]
    body = (
        "".join("data: " + json.dumps({"choices": [{"delta": delta}]}) + "\n\n" for delta in deltas)
        + "data: [DONE]\n\n"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test", base_url="https://example.test/v1"),
            client=client,
        )
        events = [
            event
            async for event in provider.stream_response(
                model="deepseek-ai/DeepSeek-V4-Flash", system="", messages=[], tools=[]
            )
        ]
    assert isinstance(events[-1], AssistantDoneEvent)
    message = events[-1].message
    assert len(message.content) == 2
    assert message.text == "Done. Private repo created."
    assert message.thinking_text == "Done. Created and pushed."
    thinking = next(block for block in message.content if isinstance(block, ThinkingContent))
    assert thinking.thinking_signature == "reasoning_content"
    for kind in ("text", "thinking"):
        starts = [event for event in events if event.type == f"{kind}_start"]
        ends = [event for event in events if event.type == f"{kind}_end"]
        fragments = [event for event in events if event.type == f"{kind}_delta"]
        assert len(starts) == len(ends) == 1
        assert {event.content_index for event in fragments} == {starts[0].content_index}
        assert ends[0].content_index == starts[0].content_index
    first_text = next(event for event in events if isinstance(event, TextDeltaEvent))
    assert first_text.partial.text == "Done. Pr"
