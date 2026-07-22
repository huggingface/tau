from collections.abc import AsyncIterator, Mapping
from json import loads

import httpx
import pytest

from tau_agent import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    SimpleCancellationToken,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.messages import assistant_content
from tau_agent.types import JSONValue
from tau_ai import (
    AnthropicConfig,
    AnthropicProvider,
    AssistantDoneEvent,
    AssistantErrorEvent,
    AssistantStartEvent,
    FakeProvider,
    GoogleGenerativeAIProvider,
    OpenAICodexConfig,
    OpenAICodexCredentials,
    OpenAICodexProvider,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    RuntimeModelLimits,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallEndEvent,
    openai_compatible_config_from_env,
)


async def _collect(stream: AsyncIterator[object]) -> list[object]:
    return [event async for event in stream]


def _provider_tool(
    name: str,
    description: str,
    parameters: Mapping[str, JSONValue],
) -> AgentTool:
    async def execute(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
        on_update: object | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        return AgentToolResult(content=str(arguments))

    return AgentTool(
        name=name,
        label=name,
        description=description,
        parameters=parameters,
        execute_fn=execute,  # type: ignore[arg-type]
    )


@pytest.mark.anyio
async def test_fake_provider_replays_scripted_events() -> None:
    start = AssistantMessage(model="fake-model")
    final = AssistantMessage(content="hello", model="fake-model")
    scripted = [
        AssistantStartEvent(partial=start),
        TextDeltaEvent(content_index=0, delta="hello", partial=final),
        AssistantDoneEvent(reason="stop", message=final),
    ]
    provider = FakeProvider([scripted])

    events = await _collect(
        provider.stream_response(
            model="fake-model",
            system="system prompt",
            messages=[UserMessage(content="hi")],
            tools=[],
        )
    )

    assert events == scripted
    assert provider.calls[0][0] == "fake-model"
    assert provider.calls[0][1] == "system prompt"


def test_openai_compatible_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1/")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "2")
    monkeypatch.setenv("OPENAI_MAX_RETRY_DELAY_SECONDS", "0.25")

    config = openai_compatible_config_from_env()

    assert config.api_key == "test-key"
    assert config.base_url == "https://example.test/v1"
    assert config.timeout_seconds == 12.5
    assert config.max_retries == 2
    assert config.max_retry_delay_seconds == 0.25


def test_openai_compatible_config_from_env_rejects_invalid_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "0")

    with pytest.raises(RuntimeError, match="greater than 0"):
        openai_compatible_config_from_env()


def test_openai_compatible_config_from_env_rejects_invalid_retry_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "-1")

    with pytest.raises(RuntimeError, match="0 or greater"):
        openai_compatible_config_from_env()


@pytest.mark.anyio
async def test_openai_compatible_provider_uses_configured_timeout() -> None:
    provider = OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            api_key="test-key",
            base_url="https://example.test/v1",
            timeout_seconds=7.5,
        )
    )
    try:
        client = provider._get_client()

        assert client.timeout.connect == 7.5
        assert client.timeout.read == 7.5
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_openai_compatible_provider_formats_request_and_streams_text() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
                headers={"X-HF-Bill-To": "my-org"},
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_delta",
        "text_end",
        "done",
    ]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.text == "Hello"
    assert events[-1].reason == "stop"

    request = requests[0]
    assert request.url == "https://example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-key"
    assert request.headers["x-hf-bill-to"] == "my-org"

    payload = loads(request.content)
    assert payload["model"] == "test-model"
    assert payload["stream"] is True
    assert "reasoning_effort" not in payload
    assert payload["messages"] == [
        {"role": "system", "content": "You are Tau."},
        {"role": "user", "content": "Say hello"},
    ]


@pytest.mark.anyio
async def test_openai_compatible_provider_includes_configured_reasoning_effort() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
                reasoning_effort="high",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent)
    assert loads(requests[0].content)["reasoning_effort"] == "high"


@pytest.mark.anyio
async def test_openai_compatible_provider_includes_openrouter_provider_routing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                compat={"openrouterProvider": {"ignore": ["Nebius"]}},
            ),
            client=client,
        )

        await _collect(
            provider.stream_response(
                model="nvidia/nemotron-3-super-120b-a12b",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert loads(requests[0].content)["provider"] == {"ignore": ["Nebius"]}


@pytest.mark.anyio
async def test_openai_compatible_provider_supports_nested_reasoning_effort_parameter() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
                reasoning_effort="high",
                reasoning_effort_parameter="reasoning.effort",
            ),
            client=client,
        )

        # A model served over /chat/completions (not gpt-5.5/5.4/codex, which
        # route to /v1/responses) exercises the nested reasoning.effort payload.
        await _collect(
            provider.stream_response(
                model="custom-reasoner",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert requests[0].url == "https://example.test/v1/chat/completions"
    assert loads(requests[0].content)["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in loads(requests[0].content)


@pytest.mark.anyio
async def test_google_provider_sends_system_instruction_at_top_level() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]},'
                '"finishReason":"STOP"}]}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleGenerativeAIProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://generativelanguage.googleapis.com/v1beta",
                reasoning_effort="low",
            ),
            client=client,
        )

        await _collect(
            provider.stream_response(
                model="gemini-2.5-flash",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    payload = loads(requests[0].content)
    assert payload["systemInstruction"] == {"parts": [{"text": "You are Tau."}]}
    assert "systemInstruction" not in payload["generationConfig"]
    assert payload["generationConfig"]["thinkingConfig"] == {
        "includeThoughts": True,
        "thinkingBudget": 2048,
    }


@pytest.mark.anyio
async def test_google_provider_round_trips_thought_signature() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"candidates":[{"content":{"parts":[{"functionCall":'
                '{"id":"call-1","name":"bash","args":{"command":"ls"}},'
                '"thoughtSignature":"sig-123"}]},"finishReason":"STOP"}]}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleGenerativeAIProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://generativelanguage.googleapis.com/v1beta",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gemini-2.5-flash",
                system="",
                messages=[UserMessage(content="List files")],
                tools=[],
            )
        )

        assistant_message = events[-1].message
        assert assistant_message.tool_calls[0].thought_signature == "sig-123"

        await _collect(
            provider.stream_response(
                model="gemini-2.5-flash",
                system="",
                messages=[
                    UserMessage(content="List files"),
                    assistant_message,
                    ToolResultMessage(
                        tool_call_id="call-1", tool_name="bash", content=[TextContent(text="a.py")]
                    ),
                ],
                tools=[],
            )
        )

    second_payload = loads(requests[1].content)
    tool_call_part = second_payload["contents"][1]["parts"][0]
    assert tool_call_part["thoughtSignature"] == "sig-123"


@pytest.mark.anyio
async def test_google_provider_strips_unsupported_schema_keywords_from_tools() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]},'
                '"finishReason":"STOP"}]}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    tool = _provider_tool(
        "bash",
        "Run a shell command.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "command": {"type": "string"},
                "env": {
                    "type": "array",
                    "items": {"type": "string", "additionalProperties": False},
                },
            },
        },
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleGenerativeAIProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://generativelanguage.googleapis.com/v1beta",
            ),
            client=client,
        )

        await _collect(
            provider.stream_response(
                model="gemini-2.5-flash",
                system="",
                messages=[UserMessage(content="List files")],
                tools=[tool],
            )
        )

    payload = loads(requests[0].content)
    parameters = payload["tools"][0]["functionDeclarations"][0]["parameters"]
    assert "additionalProperties" not in parameters
    assert "additionalProperties" not in parameters["properties"]["env"]["items"]
    assert parameters["properties"]["command"] == {"type": "string"}


@pytest.mark.anyio
async def test_openai_compatible_provider_streams_reasoning_content() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"reasoning_content":"plan "}}]}\n\n'
                'data: {"choices":[{"delta":{"reasoning_content":"steps"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]
    thinking_events = [event for event in events if isinstance(event, ThinkingDeltaEvent)]
    assert [event.delta for event in thinking_events] == ["plan ", "steps"]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.text == "done"
    assert [block.type for block in events[-1].message.content] == ["thinking", "text"]
    assert events[-1].message.thinking_text == "plan steps"
    thinking = events[-1].message.content[0]
    assert isinstance(thinking, ThinkingContent)
    assert thinking.thinking_signature == "reasoning_content"


@pytest.mark.anyio
async def test_openai_compatible_provider_replays_persisted_reasoning() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"next"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    prior = AssistantMessage(
        content=[
            ThinkingContent(thinking="prior plan", thinking_signature="reasoning_content"),
            TextContent(text="prior answer"),
        ]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )
        await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="first"), prior],
                tools=[],
            )
        )

    payload = loads(requests[0].content)
    replay = payload["messages"][-1]
    assert replay["content"] == "prior answer"
    assert replay["reasoning_content"] == "prior plan"


@pytest.mark.anyio
async def test_openai_compatible_provider_streams_tool_calls() -> None:
    tool = _provider_tool(
        "read",
        "Read a file.",
        {"type": "object", "properties": {"path": {"type": "string"}}},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = loads(request.content)
        assert payload["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file.",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
                '"function":{"name":"read","arguments":"{\\"path\\":"}}]}}]}\n\n'
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                '"function":{"arguments":"\\"README.md\\"}"}}]},"finish_reason":"tool_calls"}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Read README.md")],
                tools=[tool],
            )
        )

    tool_call_events = [event for event in events if isinstance(event, ToolCallEndEvent)]

    assert [event.tool_call for event in tool_call_events] == [
        ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    ]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.tool_calls == (
        ToolCall(id="call-1", name="read", arguments={"path": "README.md"}),
    )
    assert events[-1].reason == "toolUse"


@pytest.mark.anyio
async def test_openai_compatible_provider_retries_transient_status() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(500, text="try again")
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
                max_retries=1,
                max_retry_delay_seconds=0,
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert len(requests) == 2
    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]


@pytest.mark.anyio
async def test_openai_compatible_provider_cancellation_stops_retry_backoff() -> None:
    requests: list[httpx.Request] = []
    signal = SimpleCancellationToken()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(503, text="try later")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
                max_retries=2,
                max_retry_delay_seconds=1,
            ),
            client=client,
        )

        signal.cancel()
        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
                signal=signal,
            )
        )

    assert len(requests) == 1
    assert [event.type for event in events] == ["start", "error"]
    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].reason == "error"


@pytest.mark.anyio
async def test_openai_compatible_provider_does_not_retry_non_transient_status() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            400,
            json={"error": {"message": "The selected model is unavailable."}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                provider_name="test-openai",
                base_url="https://example.test/v1",
                max_retries=3,
                max_retry_delay_seconds=0,
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert len(requests) == 1
    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].error.error_message == (
        "test-openai request failed with status 400 for model test-model: "
        "The selected model is unavailable."
    )
    assert events[-1].error.diagnostics[0].details == {
        "status_code": 400,
        "body": '{"error":{"message":"The selected model is unavailable."}}',
        "attempts": 1,
    }


@pytest.mark.anyio
async def test_openai_compatible_provider_includes_plain_http_error_body_in_message() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request details")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                provider_name="test-openai",
                base_url="https://example.test/v1",
                max_retries=0,
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].error.error_message == (
        "test-openai request failed with status 400 for model test-model: bad request details"
    )
    assert events[-1].error.diagnostics[0].details == {
        "status_code": 400,
        "body": "bad request details",
        "attempts": 1,
    }


@pytest.mark.anyio
async def test_openai_codex_provider_discovers_and_caches_live_model_limits() -> None:
    requests: list[httpx.Request] = []

    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "slug": "gpt-5.6-sol",
                        "context_window": 372_000,
                        "max_context_window": 372_000,
                        "effective_context_window_percent": 95,
                        "auto_compact_token_limit": 330_000,
                        "max_output_tokens": 128_000,
                    },
                    {"slug": "invalid", "context_window": -1},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
                client_version="0.2.0",
            ),
            client=client,
        )

        limits = await provider.discover_model_limits("gpt-5.6-sol")
        cached = await provider.discover_model_limits("gpt-5.6-sol")

    assert limits == RuntimeModelLimits(
        context_window=372_000,
        max_output_tokens=128_000,
        effective_context_window_percent=95,
        auto_compact_token_limit=330_000,
    )
    assert cached == limits
    assert len(requests) == 1
    assert str(requests[0].url) == (
        "https://chatgpt.test/backend-api/codex/models?client_version=0.2.0"
    )
    assert requests[0].headers["authorization"] == "Bearer access-token"
    assert requests[0].headers["chatgpt-account-id"] == "account-1"
    assert requests[0].headers["accept"] == "application/json"


@pytest.mark.anyio
async def test_openai_codex_provider_includes_http_error_detail_in_message() -> None:
    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "The requested model does not exist."}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
                provider_name="openai-codex",
                max_retries=0,
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].error.error_message == (
        "openai-codex request failed with status 400 for model gpt-5.5: "
        "The requested model does not exist."
    )
    assert events[-1].error.diagnostics[0].details == {
        "status_code": 400,
        "body": '{"error":{"message":"The requested model does not exist."}}',
        "attempts": 1,
    }


@pytest.mark.anyio
async def test_openai_codex_provider_includes_plain_http_error_body_in_message() -> None:
    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request details")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
                provider_name="openai-codex",
                max_retries=0,
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].error.error_message == (
        "openai-codex request failed with status 400 for model gpt-5.5: bad request details"
    )
    assert events[-1].error.diagnostics[0].details == {
        "status_code": 400,
        "body": "bad request details",
        "attempts": 1,
    }


_CODEX_OVERLOAD_ERROR_SSE = (
    'data: {"type":"error","error":{"type":"service_unavailable_error",'
    '"code":"server_is_overloaded","message":"Our servers are currently '
    'overloaded. Please try again later.","param":null},"sequence_number":2}\n\n'
)

_CODEX_TEXT_SSE = (
    'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
    'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
)


@pytest.mark.anyio
async def test_openai_codex_provider_surfaces_nested_stream_error_message() -> None:
    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_CODEX_OVERLOAD_ERROR_SSE,
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
                provider_name="openai-codex",
                max_retries=0,
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].error.error_message == (
        "Our servers are currently overloaded. Please try again later."
    )
    assert events[-1].error.diagnostics[0].details == {
        "event": {
            "type": "error",
            "error": {
                "type": "service_unavailable_error",
                "code": "server_is_overloaded",
                "message": "Our servers are currently overloaded. Please try again later.",
                "param": None,
            },
            "sequence_number": 2,
        }
    }


@pytest.mark.anyio
async def test_openai_codex_provider_retries_transient_stream_error() -> None:
    requests: list[httpx.Request] = []

    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = _CODEX_OVERLOAD_ERROR_SSE if len(requests) == 1 else _CODEX_TEXT_SSE
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
                provider_name="openai-codex",
                max_retries=1,
                max_retry_delay_seconds=0,
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert len(requests) == 2
    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]


@pytest.mark.anyio
async def test_openai_codex_provider_retries_transient_response_failed() -> None:
    requests: list[httpx.Request] = []

    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = (
            'data: {"type":"response.failed","response":{"status":"failed",'
            '"error":{"code":"server_is_overloaded",'
            '"message":"The server is overloaded."}}}\n\n'
            if len(requests) == 1
            else _CODEX_TEXT_SSE
        )
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
                provider_name="openai-codex",
                max_retries=1,
                max_retry_delay_seconds=0,
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert len(requests) == 2
    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]


@pytest.mark.anyio
async def test_openai_codex_provider_surfaces_stream_error_after_retry_exhaustion() -> None:
    requests: list[httpx.Request] = []

    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=_CODEX_OVERLOAD_ERROR_SSE,
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
                provider_name="openai-codex",
                max_retries=1,
                max_retry_delay_seconds=0,
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    assert len(requests) == 2
    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].error.error_message == (
        "Our servers are currently overloaded. Please try again later."
    )


@pytest.mark.anyio
async def test_openai_codex_provider_does_not_retry_non_transient_stream_error() -> None:
    requests: list[httpx.Request] = []

    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"type":"error","error":{"type":"invalid_request_error",'
                '"code":"invalid_api_key","message":"Invalid API key.","param":null},'
                '"sequence_number":1}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
                provider_name="openai-codex",
                max_retries=2,
                max_retry_delay_seconds=0,
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    assert len(requests) == 1
    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].error.error_message == "Invalid API key."


@pytest.mark.anyio
async def test_openai_codex_provider_formats_request_and_streams_text() -> None:
    requests: list[httpx.Request] = []

    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = loads(request.content)
        assert payload["model"] == "gpt-5.5"
        assert payload["store"] is False
        assert payload["stream"] is True
        assert payload["instructions"] == "You are Tau."
        assert payload["input"] == [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "Say hello"}],
            }
        ]
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_text.delta","delta":"Hel"}\n\n'
                'data: {"type":"response.output_text.delta","delta":"lo"}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
                headers={"X-Test": "enabled"},
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_delta",
        "text_end",
        "done",
    ]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.text == "Hello"
    assert events[-1].reason == "stop"

    request = requests[0]
    assert request.url == "https://chatgpt.test/backend-api/codex/responses"
    assert request.headers["authorization"] == "Bearer access-token"
    assert request.headers["chatgpt-account-id"] == "account-1"
    assert request.headers["originator"] == "tau"
    assert request.headers["openai-beta"] == "responses=experimental"
    assert request.headers["x-test"] == "enabled"


@pytest.mark.anyio
async def test_openai_codex_provider_includes_configured_reasoning_effort() -> None:
    requests: list[httpx.Request] = []

    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text='data: {"type":"response.completed","response":{"status":"completed"}}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
                reasoning_effort="high",
            ),
            client=client,
        )

        await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    assert loads(requests[0].content)["reasoning"] == {
        "effort": "high",
        "summary": "auto",
    }


@pytest.mark.anyio
async def test_openai_codex_provider_omits_reasoning_when_unset() -> None:
    requests: list[httpx.Request] = []

    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text='data: {"type":"response.completed","response":{"status":"completed"}}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
            ),
            client=client,
        )

        await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    assert "reasoning" not in loads(requests[0].content)


@pytest.mark.anyio
async def test_openai_codex_provider_streams_reasoning_deltas() -> None:
    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.reasoning.delta","delta":"trace "}\n\n'
                'data: {"type":"response.reasoning_text.delta","delta":"details"}\n\n'
                'data: {"type":"response.output_text.delta","delta":"Done"}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say done")],
                tools=[],
            )
        )

    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]
    thinking_events = [event for event in events if isinstance(event, ThinkingDeltaEvent)]
    assert [event.delta for event in thinking_events] == ["trace ", "details"]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.text == "Done"


@pytest.mark.anyio
async def test_openai_codex_provider_streams_tool_calls() -> None:
    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    tool = _provider_tool(
        "read",
        "Read a file.",
        {"type": "object", "properties": {"path": {"type": "string"}}},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = loads(request.content)
        assert payload["tools"] == [
            {
                "type": "function",
                "name": "read",
                "description": "Read a file.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                "strict": None,
            }
        ]
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_item.added",'
                '"item":{"type":"function_call","id":"fc-1","call_id":"call-1","name":"read"}}\n\n'
                'data: {"type":"response.function_call_arguments.delta","delta":"{\\"path\\":"}\n\n'
                'data: {"type":"response.function_call_arguments.done",'
                '"arguments":"{\\"path\\":\\"README.md\\"}"}\n\n'
                'data: {"type":"response.output_item.done",'
                '"item":{"type":"function_call","id":"fc-1","call_id":"call-1",'
                '"name":"read","arguments":"{\\"path\\":\\"README.md\\"}"}}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Read README.md")],
                tools=[tool],
            )
        )

    tool_call_events = [event for event in events if isinstance(event, ToolCallEndEvent)]

    assert [event.tool_call for event in tool_call_events] == [
        ToolCall(id="call-1|fc-1", name="read", arguments={"path": "README.md"})
    ]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.tool_calls == (
        ToolCall(id="call-1|fc-1", name="read", arguments={"path": "README.md"}),
    )


@pytest.mark.anyio
async def test_openai_codex_provider_routes_parallel_tool_argument_streams() -> None:
    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_item.added","output_index":0,'
                '"item":{"type":"function_call","id":"fc-1","call_id":"call-1","name":"read"}}\n\n'
                'data: {"type":"response.output_item.added","output_index":1,'
                '"item":{"type":"function_call","id":"fc-2","call_id":"call-2","name":"run"}}\n\n'
                'data: {"type":"response.function_call_arguments.delta",'
                '"item_id":"fc-1","delta":"{\\"path\\":"}\n\n'
                'data: {"type":"response.function_call_arguments.delta",'
                '"item_id":"fc-2","delta":"{\\"cmd\\":"}\n\n'
                'data: {"type":"response.function_call_arguments.done",'
                '"item_id":"fc-1","arguments":"{\\"path\\":\\"README.md\\"}"}\n\n'
                'data: {"type":"response.output_item.done","output_index":0,'
                '"item":{"type":"function_call","id":"fc-1","call_id":"call-1","name":"read"}}\n\n'
                'data: {"type":"response.function_call_arguments.done",'
                '"item_id":"fc-2","arguments":"{\\"cmd\\":\\"pwd\\"}"}\n\n'
                'data: {"type":"response.output_item.done","output_index":1,'
                '"item":{"type":"function_call","id":"fc-2","call_id":"call-2","name":"run"}}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Use two tools")],
                tools=[],
            )
        )

    tool_call_events = [event for event in events if isinstance(event, ToolCallEndEvent)]

    assert [event.tool_call for event in tool_call_events] == [
        ToolCall(id="call-1|fc-1", name="read", arguments={"path": "README.md"}),
        ToolCall(id="call-2|fc-2", name="run", arguments={"cmd": "pwd"}),
    ]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.tool_calls == (
        ToolCall(id="call-1|fc-1", name="read", arguments={"path": "README.md"}),
        ToolCall(id="call-2|fc-2", name="run", arguments={"cmd": "pwd"}),
    )


@pytest.mark.anyio
async def test_anthropic_provider_formats_request_and_streams_text() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"type":"message_start","message":{"content":[]}}\n\n'
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"Hel"}}\n\n'
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"lo"}}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(
                api_key="test-key",
                base_url="https://api.anthropic.test/v1",
                headers={"anthropic-beta": "fine-grained-tool-streaming-2025-05-14"},
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="claude-test",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_delta",
        "text_end",
        "done",
    ]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.text == "Hello"
    assert events[-1].reason == "stop"

    request = requests[0]
    assert request.url == "https://api.anthropic.test/v1/messages"
    assert request.headers["x-api-key"] == "test-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    assert request.headers["anthropic-beta"] == "fine-grained-tool-streaming-2025-05-14"

    payload = loads(request.content)
    assert payload["model"] == "claude-test"
    assert payload["stream"] is True
    assert payload["system"] == "You are Tau."
    assert payload["messages"] == [{"role": "user", "content": "Say hello"}]


@pytest.mark.anyio
async def test_anthropic_provider_includes_configured_thinking_budget() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text='data: {"type":"message_stop"}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(
                api_key="test-key",
                base_url="https://api.anthropic.test/v1",
                thinking_budget_tokens=8192,
            ),
            client=client,
        )

        await _collect(
            provider.stream_response(
                model="claude-test",
                system="You are Tau.",
                messages=[UserMessage(content="Say hello")],
                tools=[],
            )
        )

    payload = loads(requests[0].content)
    assert payload["max_tokens"] == 9216
    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 8192}


@pytest.mark.anyio
async def test_anthropic_provider_streams_thinking_deltas() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"message_start","message":{"content":[]}}\n\n'
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"thinking_delta","thinking":"trace "}}\n\n'
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"thinking_delta","thinking":"details"}}\n\n'
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"Done"}}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(api_key="test-key", base_url="https://api.anthropic.test/v1"),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="claude-test",
                system="You are Tau.",
                messages=[UserMessage(content="Say done")],
                tools=[],
            )
        )

    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]
    thinking_events = [event for event in events if isinstance(event, ThinkingDeltaEvent)]
    assert [event.delta for event in thinking_events] == ["trace ", "details"]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.text == "Done"


@pytest.mark.anyio
async def test_anthropic_provider_retries_transient_status_with_event() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(
            200,
            text=(
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"ok"}}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(
                api_key="test-key",
                base_url="https://api.anthropic.test/v1",
                max_retries=1,
                max_retry_delay_seconds=0,
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

    assert len(requests) == 2
    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]


@pytest.mark.anyio
async def test_anthropic_provider_retries_overloaded_529() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(529, text="overloaded")
        return httpx.Response(
            200,
            text=(
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"ok"}}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(
                api_key="test-key",
                base_url="https://api.anthropic.test/v1",
                max_retries=1,
                max_retry_delay_seconds=0,
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

    assert len(requests) == 2
    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]


@pytest.mark.anyio
async def test_anthropic_provider_includes_http_error_detail_in_message() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "model: invalid-model is not supported"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(
                api_key="test-key",
                provider_name="anthropic",
                base_url="https://api.anthropic.test/v1",
                max_retries=0,
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

    assert isinstance(events[-1], AssistantErrorEvent)
    assert events[-1].error.error_message == (
        "anthropic request failed with status 400 for model claude-test: "
        "model: invalid-model is not supported"
    )
    assert events[-1].error.diagnostics[0].details == {
        "status_code": 400,
        "body": '{"error":{"message":"model: invalid-model is not supported"}}',
        "attempts": 1,
    }


def _weather_tool() -> AgentTool:
    return _provider_tool(
        "get_weather",
        "Get current weather for a city.",
        {"type": "object", "properties": {"city": {"type": "string"}}},
    )


def test_use_responses_api_routes_only_restricted_models() -> None:
    from tau_ai.openai_compatible import _use_responses_api

    assert _use_responses_api("gpt-5.5") is True
    assert _use_responses_api("gpt-5.5-pro") is True
    assert _use_responses_api("gpt-5.4") is True
    assert _use_responses_api("gpt-5.3-codex") is True
    assert _use_responses_api("GPT-5.5") is True
    assert _use_responses_api("gpt-5.1") is False
    assert _use_responses_api("gpt-5") is False
    assert _use_responses_api("gpt-4o") is False
    assert _use_responses_api("test-model") is False


@pytest.mark.anyio
async def test_responses_api_formats_request_for_restricted_model() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_text.delta","delta":"Sun"}\n\n'
                'data: {"type":"response.output_text.delta","delta":"ny"}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    messages = [
        UserMessage(content="weather in Paris?"),
        AssistantMessage(
            content=assistant_content(
                "", [ToolCall(id="call_1", name="get_weather", arguments={"city": "Paris"})]
            )
        ),
        ToolResultMessage(
            tool_call_id="call_1",
            tool_name="get_weather",
            content=[TextContent(text='{"temp_c": 19}')],
        ),
        UserMessage(content="summarize"),
    ]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
                reasoning_effort="medium",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=messages,
                tools=[_weather_tool()],
            )
        )

    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_delta",
        "text_end",
        "done",
    ]
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.text == "Sunny"
    assert events[-1].reason == "stop"

    request = requests[0]
    assert request.url == "https://example.test/v1/responses"

    payload = loads(request.content)
    assert payload["model"] == "gpt-5.5"
    assert payload["stream"] is True
    assert payload["store"] is False
    assert payload["instructions"] == "You are Tau."
    assert payload["reasoning"] == {"effort": "medium", "summary": "auto"}
    # Responses-API tools are flat (no nested "function" object).
    assert payload["tools"] == [
        {
            "type": "function",
            "name": "get_weather",
            "description": "Get current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        }
    ]
    # The assistant turn has empty content, so only its function_call appears.
    assert payload["input"][0] == {"role": "user", "content": "weather in Paris?"}
    function_call = payload["input"][1]
    assert function_call["type"] == "function_call"
    assert function_call["call_id"] == "call_1"
    assert function_call["name"] == "get_weather"
    assert loads(function_call["arguments"]) == {"city": "Paris"}
    assert payload["input"][2] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"temp_c": 19}',
    }
    assert payload["input"][3] == {"role": "user", "content": "summarize"}


@pytest.mark.anyio
async def test_responses_api_parses_streamed_tool_call() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_item.added","output_index":0,'
                '"item":{"id":"fc_1","type":"function_call","call_id":"call_abc",'
                '"name":"get_weather","arguments":""}}\n\n'
                'data: {"type":"response.function_call_arguments.delta",'
                '"item_id":"fc_1","delta":"{\\"city\\":"}\n\n'
                'data: {"type":"response.function_call_arguments.delta",'
                '"item_id":"fc_1","delta":"\\"Paris\\"}"}\n\n'
                'data: {"type":"response.function_call_arguments.done",'
                '"item_id":"fc_1","arguments":"{\\"city\\":\\"Paris\\"}"}\n\n'
                'data: {"type":"response.output_item.done","output_index":0,'
                '"item":{"id":"fc_1","type":"function_call","call_id":"call_abc",'
                '"name":"get_weather","arguments":"{\\"city\\":\\"Paris\\"}"}}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="weather?")],
                tools=[_weather_tool()],
            )
        )

    tool_call_events = [e for e in events if isinstance(e, ToolCallEndEvent)]
    assert len(tool_call_events) == 1
    assert tool_call_events[0].tool_call.id == "call_abc"
    assert tool_call_events[0].tool_call.name == "get_weather"
    assert tool_call_events[0].tool_call.arguments == {"city": "Paris"}

    end = events[-1]
    assert isinstance(end, AssistantDoneEvent)
    assert len(end.message.tool_calls) == 1
    assert end.message.tool_calls[0].id == "call_abc"
    assert end.message.tool_calls[0].arguments == {"city": "Paris"}
    assert end.reason == "toolUse"


@pytest.mark.anyio
async def test_responses_api_streams_refusal_as_text() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.refusal.delta","delta":"I can"}\n\n'
                'data: {"type":"response.refusal.delta","delta":"not help with that."}\n\n'
                'data: {"type":"response.refusal.done",'
                '"refusal":"I cannot help with that."}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="unsafe request")],
                tools=[],
            )
        )

    assert [event.type for event in events] == [
        "start",
        "text_start",
        "text_delta",
        "text_delta",
        "text_end",
        "done",
    ]
    text_deltas = [e.delta for e in events if isinstance(e, TextDeltaEvent)]
    assert text_deltas == ["I can", "not help with that."]
    end = events[-1]
    assert isinstance(end, AssistantDoneEvent)
    assert end.message.text == "I cannot help with that."
    assert end.reason == "stop"


@pytest.mark.anyio
async def test_responses_api_streams_reasoning_summary_as_thinking() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.reasoning_summary_text.delta",'
                '"delta":"Considering"}\n\n'
                'data: {"type":"response.output_text.delta","delta":"Answer"}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
                reasoning_effort="high",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="think")],
                tools=[],
            )
        )

    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]
    thinking = next(e for e in events if isinstance(e, ThinkingDeltaEvent))
    assert thinking.delta == "Considering"


@pytest.mark.anyio
async def test_responses_api_omits_reasoning_when_effort_is_none() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text='data: {"type":"response.completed","response":{"status":"completed"}}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
                reasoning_effort="none",
            ),
            client=client,
        )

        await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="hi")],
                tools=[_weather_tool()],
            )
        )

    payload = loads(requests[0].content)
    # gpt-5.5 rejects tools + reasoning on /chat/completions; with thinking off
    # the reasoning field is dropped entirely so tools still work over /responses.
    assert "reasoning" not in payload
    assert "tools" in payload


@pytest.mark.anyio
async def test_responses_api_surfaces_stream_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.failed","response":{"status":"failed",'
                '"error":{"message":"model exploded"}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="hi")],
                tools=[],
            )
        )

    error_events = [e for e in events if isinstance(e, AssistantErrorEvent)]
    assert len(error_events) == 1
    assert error_events[0].error.error_message == "model exploded"
    # The raw event is preserved for debugging (code/param/type, etc.).
    assert error_events[0].error.diagnostics[0].details is not None
    assert error_events[0].error.diagnostics[0].details["event"]["type"] == "response.failed"


@pytest.mark.anyio
async def test_responses_api_orders_parallel_tool_calls_by_output_index() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_item.added","output_index":0,'
                '"item":{"id":"fc_a","type":"function_call","call_id":"call_a",'
                '"name":"get_weather","arguments":"{\\"city\\":\\"A\\"}"}}\n\n'
                'data: {"type":"response.output_item.added","output_index":1,'
                '"item":{"id":"fc_b","type":"function_call","call_id":"call_b",'
                '"name":"get_weather","arguments":"{\\"city\\":\\"B\\"}"}}\n\n'
                # Done events arrive out of order to prove sorting by output_index.
                'data: {"type":"response.output_item.done","output_index":1,'
                '"item":{"id":"fc_b","type":"function_call","call_id":"call_b",'
                '"name":"get_weather","arguments":"{\\"city\\":\\"B\\"}"}}\n\n'
                'data: {"type":"response.output_item.done","output_index":0,'
                '"item":{"id":"fc_a","type":"function_call","call_id":"call_a",'
                '"name":"get_weather","arguments":"{\\"city\\":\\"A\\"}"}}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="weather?")],
                tools=[_weather_tool()],
            )
        )

    end = events[-1]
    assert isinstance(end, AssistantDoneEvent)
    assert [tc.id for tc in end.message.tool_calls] == ["call_a", "call_b"]
    assert [tc.arguments["city"] for tc in end.message.tool_calls] == ["A", "B"]


@pytest.mark.anyio
async def test_responses_api_surfaces_top_level_error_event() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='data: {"type":"error","message":"rate limited"}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="hi")],
                tools=[],
            )
        )

    error_events = [e for e in events if isinstance(e, AssistantErrorEvent)]
    assert len(error_events) == 1
    assert error_events[0].error.error_message == "rate limited"


@pytest.mark.anyio
async def test_responses_api_maps_incomplete_status_to_length() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
                'data: {"type":"response.incomplete","response":{"status":"incomplete"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(api_key="test-key", base_url="https://example.test/v1"),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="hi")],
                tools=[],
            )
        )

    end = events[-1]
    assert isinstance(end, AssistantDoneEvent)
    assert end.message.text == "partial"
    assert end.reason == "length"


@pytest.mark.anyio
async def test_openai_compatible_provider_reports_usage() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":"stop"}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":30,"completion_tokens":5,'
                '"prompt_tokens_details":{"cached_tokens":10},'
                '"completion_tokens_details":{"reasoning_tokens":2}}}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say hi")],
                tools=[],
            )
        )

    # The request opts in to streamed usage reporting.
    assert loads(requests[0].content)["stream_options"] == {"include_usage": True}

    assert isinstance(events[-1], AssistantDoneEvent)
    usage = events[-1].message.usage
    assert usage is not None
    assert usage.input == 20  # 30 prompt - 10 cached
    assert usage.output == 5
    assert usage.cache_read == 10
    assert usage.cache_write == 0
    assert usage.reasoning == 2
    assert usage.total_tokens == 35
    assert usage.cost.total == 0


@pytest.mark.anyio
async def test_openai_codex_provider_reports_usage() -> None:
    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_text.delta","delta":"Hi"}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed",'
                '"usage":{"input_tokens":50,"output_tokens":8,"total_tokens":58,'
                '"input_tokens_details":{"cached_tokens":12},'
                '"output_tokens_details":{"reasoning_tokens":3}}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hi")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent)
    usage = events[-1].message.usage
    assert usage is not None
    assert usage.input == 38  # 50 input - 12 cached
    assert usage.output == 8
    assert usage.cache_read == 12
    assert usage.cache_write == 0
    assert usage.reasoning == 3
    assert usage.total_tokens == 58
    assert usage.cost.total == 0


@pytest.mark.anyio
async def test_openai_compatible_responses_api_reports_usage() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_text.delta","delta":"Hi"}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed",'
                '"usage":{"input_tokens":50,"output_tokens":8,"total_tokens":58,'
                '"input_tokens_details":{"cached_tokens":12},'
                '"output_tokens_details":{"reasoning_tokens":3}}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hi")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent)
    usage = events[-1].message.usage
    assert usage is not None
    assert usage.input == 38  # 50 input - 12 cached
    assert usage.output == 8
    assert usage.cache_read == 12
    assert usage.cache_write == 0
    assert usage.reasoning == 3
    assert usage.total_tokens == 58
    assert usage.cost.total == 0


@pytest.mark.anyio
async def test_anthropic_provider_reports_usage() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"message_start","message":{"content":[],"usage":'
                '{"input_tokens":100,"output_tokens":1,"cache_read_input_tokens":40,'
                '"cache_creation_input_tokens":25,'
                '"cache_creation":{"ephemeral_1h_input_tokens":10}}}}\n\n'
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"Hi"}}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
                '"usage":{"output_tokens":7}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(
                api_key="test-key",
                base_url="https://api.anthropic.test/v1",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="claude-test",
                system="You are Tau.",
                messages=[UserMessage(content="Say hi")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent)
    usage = events[-1].message.usage
    assert usage is not None
    assert usage.input == 100
    assert usage.output == 7  # updated by message_delta
    assert usage.cache_read == 40
    assert usage.cache_write == 25
    assert usage.cache_write_1h == 10
    assert usage.total_tokens == 172  # 100 + 7 + 40 + 25
    assert usage.cost.total == 0


@pytest.mark.anyio
async def test_openai_compatible_provider_can_disable_usage_in_streaming() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
                compat={"supportsUsageInStreaming": False},
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say ok")],
                tools=[],
            )
        )

    assert "stream_options" not in loads(requests[0].content)
    assert isinstance(events[-1], AssistantDoneEvent)
    assert events[-1].message.usage.total_tokens == 0


@pytest.mark.anyio
async def test_openai_compatible_provider_reads_usage_from_choice_fallback() -> None:
    # Moonshot-style: usage lives on the choice, not at the chunk top level.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":"stop",'
                '"usage":{"prompt_tokens":15,"completion_tokens":4}}]}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say hi")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent)
    usage = events[-1].message.usage
    assert usage is not None
    assert usage.input == 15
    assert usage.output == 4
    assert usage.cache_read == 0
    assert usage.total_tokens == 19


@pytest.mark.anyio
async def test_openai_compatible_provider_falls_back_to_prompt_cache_hit_tokens() -> None:
    # DeepSeek-style: cache reads come via prompt_cache_hit_tokens, and there
    # is no prompt_tokens_details.cached_tokens to prefer.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":"stop"}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":40,"completion_tokens":6,'
                '"prompt_cache_hit_tokens":16}}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say hi")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent)
    usage = events[-1].message.usage
    assert usage is not None
    assert usage.input == 24  # 40 prompt - 16 cache hits
    assert usage.cache_read == 16
    assert usage.output == 6
    assert usage.total_tokens == 46


@pytest.mark.anyio
async def test_openai_compatible_provider_reported_zero_cached_tokens_wins() -> None:
    # Nullish semantics (Pi: cached_tokens ?? prompt_cache_hit_tokens ?? 0):
    # an explicitly reported cached_tokens of 0 must not fall through to
    # prompt_cache_hit_tokens.
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":"stop"}]}\n\n'
                'data: {"choices":[],"usage":{"prompt_tokens":40,"completion_tokens":6,'
                '"prompt_tokens_details":{"cached_tokens":0},'
                '"prompt_cache_hit_tokens":16}}\n\n'
                "data: [DONE]\n\n"
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(
            OpenAICompatibleConfig(
                api_key="test-key",
                base_url="https://example.test/v1",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="test-model",
                system="You are Tau.",
                messages=[UserMessage(content="Say hi")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent)
    usage = events[-1].message.usage
    assert usage is not None
    assert usage.cache_read == 0
    assert usage.input == 40


@pytest.mark.anyio
async def test_anthropic_provider_reports_usage_from_message_delta_only() -> None:
    # No usage on message_start: the message_delta usage alone must still
    # produce a Usage (the `usage or Usage()` branch).
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"message_start","message":{"content":[]}}\n\n'
                'data: {"type":"content_block_delta","index":0,'
                '"delta":{"type":"text_delta","text":"Hi"}}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
                '"usage":{"input_tokens":12,"output_tokens":3}}\n\n'
                'data: {"type":"message_stop"}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = AnthropicProvider(
            AnthropicConfig(
                api_key="test-key",
                base_url="https://api.anthropic.test/v1",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="claude-test",
                system="You are Tau.",
                messages=[UserMessage(content="Say hi")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent)
    usage = events[-1].message.usage
    assert usage is not None
    assert usage.input == 12
    assert usage.output == 3
    assert usage.cache_read == 0
    assert usage.cache_write == 0
    assert usage.reasoning is None
    assert usage.total_tokens == 15


@pytest.mark.anyio
async def test_openai_codex_provider_leaves_reasoning_none_when_unreported() -> None:
    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.output_text.delta","delta":"Hi"}\n\n'
                'data: {"type":"response.completed","response":{"status":"completed",'
                '"usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICodexProvider(
            OpenAICodexConfig(
                credential_resolver=credentials,
                base_url="https://chatgpt.test/backend-api",
            ),
            client=client,
        )

        events = await _collect(
            provider.stream_response(
                model="gpt-5.5",
                system="You are Tau.",
                messages=[UserMessage(content="Say hi")],
                tools=[],
            )
        )

    assert isinstance(events[-1], AssistantDoneEvent)
    usage = events[-1].message.usage
    assert usage is not None
    assert usage.reasoning is None
    assert usage.cache_read == 0
    assert usage.total_tokens == 12
