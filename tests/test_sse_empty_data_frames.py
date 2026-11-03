import json
from collections.abc import AsyncIterator

import httpx
import pytest

from tau_agent import UserMessage
from tau_ai import AssistantDoneEvent, OpenAICompatibleConfig, OpenAICompatibleProvider
from tau_ai.google import GoogleGenerativeAIProvider
from tau_ai.mistral import MistralConversationsProvider


def _sse(*frames: str) -> str:
    return "".join(f"{frame}\n\n" for frame in frames)


def _chat_chunk(**delta: object) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


def _google_chunk(text: str, finish: str | None = None) -> str:
    candidate: dict[str, object] = {"content": {"parts": [{"text": text}]}}
    if finish is not None:
        candidate["finishReason"] = finish
    return "data: " + json.dumps({"candidates": [candidate]})


def _mistral_chunk(**delta: object) -> str:
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


async def _collect(stream: AsyncIterator[object]) -> list[object]:
    return [event async for event in stream]


_CONFIG = OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1")

_CASES = {
    "openai_compatible": (
        OpenAICompatibleProvider,
        _sse(
            _chat_chunk(role="assistant"),
            "data:",
            _chat_chunk(content="ok"),
            "data: [DONE]",
        ),
        "model-x",
    ),
    "google": (
        GoogleGenerativeAIProvider,
        _sse(_google_chunk("o"), "data:", _google_chunk("k", "STOP")),
        "gemini-x",
    ),
    "mistral": (
        MistralConversationsProvider,
        _sse(
            _mistral_chunk(role="assistant"),
            "data:",
            _mistral_chunk(content="ok"),
            "data: [DONE]",
        ),
        "mistral-x",
    ),
}


@pytest.mark.anyio
@pytest.mark.parametrize("name", sorted(_CASES))
async def test_provider_ignores_empty_data_frames(name: str) -> None:
    provider_cls, body, model = _CASES[name]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = provider_cls(_CONFIG, client=client)
        events = await _collect(
            provider.stream_response(
                model=model,
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent), events[-1]
    assert events[-1].message.text == "ok"
