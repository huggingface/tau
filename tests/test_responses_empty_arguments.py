from json import dumps, loads

import httpx
import pytest

from tau_agent import AssistantMessage, ToolResultMessage, UserMessage
from tau_ai import (
    AssistantDoneEvent,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ToolCallEndEvent,
)


def item_event(kind: str, index: int, item_id: str, call_id: str, arguments: str = "") -> dict:
    return {
        "type": f"response.output_item.{kind}",
        "output_index": index,
        "item": {
            "type": "function_call",
            "id": item_id,
            "call_id": call_id,
            "name": "get_weather",
            "arguments": arguments,
        },
    }


async def stream_calls(chunks: list[dict]) -> AssistantMessage:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(loads(request.content))
        events = chunks if len(requests) == 1 else []
        completed = {"type": "response.completed", "response": {"status": "completed"}}
        return httpx.Response(
            200,
            text="".join(f"data: {dumps(event)}\n\n" for event in [*events, completed]),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )
        events = [
            event
            async for event in provider.stream_response(
                model="gpt-5.5", system="", messages=[UserMessage(content="weather?")], tools=[]
            )
        ]
        end = events[-1]
        assert isinstance(end, AssistantDoneEvent)
        calls = end.message.tool_calls
        assert (
            tuple(event.tool_call for event in events if isinstance(event, ToolCallEndEvent))
            == calls
        )
        assert all(call.name == "get_weather" for call in calls)
        history = [UserMessage(content="weather?"), end.message]
        history.extend(
            ToolResultMessage(tool_call_id=call.id, tool_name=call.name, content="sunny")
            for call in calls
        )
        _ = [
            event
            async for event in provider.stream_response(
                model="gpt-5.5", system="", messages=history, tools=[]
            )
        ]

    replayed = [item for item in requests[1]["input"] if item.get("type") == "function_call"]
    assert [(item["call_id"], item["name"], loads(item["arguments"])) for item in replayed] == [
        (call.id, call.name, call.arguments) for call in calls
    ]
    return end.message


@pytest.mark.anyio
@pytest.mark.parametrize("final_kind", ["arguments", "item", "both"])
async def test_responses_empty_final_arguments_preserve_deltas(final_kind: str) -> None:
    chunks = [
        item_event("added", 0, "fc_a", "call_a"),
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_a",
            "delta": '{"city":"Paris"}',
        },
    ]
    if final_kind in ("arguments", "both"):
        chunks.append(
            {"type": "response.function_call_arguments.done", "item_id": "fc_a", "arguments": ""}
        )
    if final_kind in ("item", "both"):
        chunks.append(item_event("done", 0, "fc_a", "call_a"))
    message = await stream_calls(chunks)
    assert message.tool_calls[0].arguments == {"city": "Paris"}


@pytest.mark.anyio
async def test_responses_nonempty_final_arguments_override_deltas() -> None:
    message = await stream_calls(
        [
            item_event("added", 0, "fc_a", "call_a"),
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_a",
                "delta": '{"city":"Paris"}',
            },
            item_event("done", 0, "fc_a", "call_a", '{"city":"London"}'),
        ]
    )
    assert message.tool_calls[0].arguments == {"city": "London"}
