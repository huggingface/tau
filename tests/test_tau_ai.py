from collections.abc import AsyncIterator, Mapping
from json import loads

import httpx
import pytest

from tau_agent import (
    AgentTool,
    AgentToolResult,
    AssistantMessage,
    SimpleCancellationToken,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from tau_agent.types import JSONValue
from tau_ai import (
    AnthropicConfig,
    AnthropicProvider,
    FakeProvider,
    GoogleGenerativeAIProvider,
    OpenAICodexConfig,
    OpenAICodexCredentials,
    OpenAICodexProvider,
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
    ProviderErrorEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderRetryEvent,
    ProviderTextDeltaEvent,
    ProviderThinkingDeltaEvent,
    ProviderToolCallEvent,
    openai_compatible_config_from_env,
)


async def _collect(stream: AsyncIterator[object]) -> list[object]:
    return [event async for event in stream]


@pytest.mark.anyio
async def test_fake_provider_replays_scripted_events() -> None:
    scripted = [
        ProviderResponseStartEvent(model="fake-model"),
        ProviderTextDeltaEvent(delta="hello"),
        ProviderResponseEndEvent(message={"role": "assistant", "content": "hello"}),
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
        "response_start",
        "text_delta",
        "text_delta",
        "response_end",
    ]
    assert isinstance(events[-1], ProviderResponseEndEvent)
    assert events[-1].message.content == "Hello"
    assert events[-1].finish_reason == "stop"

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

    assert isinstance(events[-1], ProviderResponseEndEvent)
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
                    ToolResultMessage(tool_call_id="call-1", name="bash", content="a.py"),
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

    async def executor(_arguments: dict[str, JSONValue]) -> AgentToolResult:
        return AgentToolResult(tool_call_id="", ok=True, content="")

    tool = AgentTool(
        name="bash",
        description="Run a shell command.",
        input_schema={
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
        executor=executor,
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
        "response_start",
        "thinking_delta",
        "thinking_delta",
        "text_delta",
        "response_end",
    ]
    thinking_events = [event for event in events if isinstance(event, ProviderThinkingDeltaEvent)]
    assert [event.delta for event in thinking_events] == ["plan ", "steps"]
    assert isinstance(events[-1], ProviderResponseEndEvent)
    assert events[-1].message.content == "done"


@pytest.mark.anyio
async def test_openai_compatible_provider_streams_tool_calls() -> None:
    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
    ) -> AgentToolResult:
        del signal
        return AgentToolResult(
            tool_call_id="call-1",
            name="read",
            ok=True,
            content=str(arguments),
        )

    tool = AgentTool(
        name="read",
        description="Read a file.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        executor=executor,
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

    tool_call_events = [event for event in events if isinstance(event, ProviderToolCallEvent)]

    assert tool_call_events == [
        ProviderToolCallEvent(
            tool_call=ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
        )
    ]
    assert isinstance(events[-1], ProviderResponseEndEvent)
    assert events[-1].message.tool_calls == [
        ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    ]
    assert events[-1].finish_reason == "tool_calls"


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
    assert isinstance(events[0], ProviderRetryEvent)
    assert events[0].attempt == 2
    assert events[0].max_attempts == 2
    assert events[0].delay_seconds == 0
    assert events[0].data == {"status_code": 500, "body": "try again"}
    assert [event.type for event in events] == [
        "retry",
        "response_start",
        "text_delta",
        "response_end",
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

        events: list[object] = []
        async for event in provider.stream_response(
            model="test-model",
            system="You are Tau.",
            messages=[UserMessage(content="Say ok")],
            tools=[],
            signal=signal,
        ):
            events.append(event)
            if isinstance(event, ProviderRetryEvent):
                signal.cancel()

    assert len(requests) == 1
    assert [event.type for event in events] == ["retry"]


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
    assert isinstance(events[-1], ProviderErrorEvent)
    assert events[-1].message == (
        "test-openai request failed with status 400 for model test-model: "
        "The selected model is unavailable."
    )
    assert events[-1].data == {
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

    assert isinstance(events[-1], ProviderErrorEvent)
    assert events[-1].message == (
        "test-openai request failed with status 400 for model test-model: bad request details"
    )
    assert events[-1].data == {
        "status_code": 400,
        "body": "bad request details",
        "attempts": 1,
    }


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

    assert isinstance(events[-1], ProviderErrorEvent)
    assert events[-1].message == (
        "openai-codex request failed with status 400 for model gpt-5.5: "
        "The requested model does not exist."
    )
    assert events[-1].data == {
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

    assert isinstance(events[-1], ProviderErrorEvent)
    assert events[-1].message == (
        "openai-codex request failed with status 400 for model gpt-5.5: bad request details"
    )
    assert events[-1].data == {
        "status_code": 400,
        "body": "bad request details",
        "attempts": 1,
    }


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
        "response_start",
        "text_delta",
        "text_delta",
        "response_end",
    ]
    assert isinstance(events[-1], ProviderResponseEndEvent)
    assert events[-1].message.content == "Hello"
    assert events[-1].finish_reason == "completed"

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
        "response_start",
        "thinking_delta",
        "thinking_delta",
        "text_delta",
        "response_end",
    ]
    thinking_events = [event for event in events if isinstance(event, ProviderThinkingDeltaEvent)]
    assert [event.delta for event in thinking_events] == ["trace ", "details"]
    assert isinstance(events[-1], ProviderResponseEndEvent)
    assert events[-1].message.content == "Done"


@pytest.mark.anyio
async def test_openai_codex_provider_streams_tool_calls() -> None:
    async def credentials() -> OpenAICodexCredentials:
        return OpenAICodexCredentials(access_token="access-token", account_id="account-1")

    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: object | None = None,
    ) -> AgentToolResult:
        del signal
        return AgentToolResult(
            tool_call_id="call-1|fc-1",
            name="read",
            ok=True,
            content=str(arguments),
        )

    tool = AgentTool(
        name="read",
        description="Read a file.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        executor=executor,
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

    tool_call_events = [event for event in events if isinstance(event, ProviderToolCallEvent)]

    assert tool_call_events == [
        ProviderToolCallEvent(
            tool_call=ToolCall(id="call-1|fc-1", name="read", arguments={"path": "README.md"})
        )
    ]
    assert isinstance(events[-1], ProviderResponseEndEvent)
    assert events[-1].message.tool_calls == [
        ToolCall(id="call-1|fc-1", name="read", arguments={"path": "README.md"})
    ]


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

    tool_call_events = [event for event in events if isinstance(event, ProviderToolCallEvent)]

    assert tool_call_events == [
        ProviderToolCallEvent(
            tool_call=ToolCall(id="call-1|fc-1", name="read", arguments={"path": "README.md"})
        ),
        ProviderToolCallEvent(
            tool_call=ToolCall(id="call-2|fc-2", name="run", arguments={"cmd": "pwd"})
        ),
    ]
    assert isinstance(events[-1], ProviderResponseEndEvent)
    assert events[-1].message.tool_calls == [
        ToolCall(id="call-1|fc-1", name="read", arguments={"path": "README.md"}),
        ToolCall(id="call-2|fc-2", name="run", arguments={"cmd": "pwd"}),
    ]


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
        "response_start",
        "text_delta",
        "text_delta",
        "response_end",
    ]
    assert isinstance(events[-1], ProviderResponseEndEvent)
    assert events[-1].message.content == "Hello"
    assert events[-1].finish_reason == "end_turn"

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
        "response_start",
        "thinking_delta",
        "thinking_delta",
        "text_delta",
        "response_end",
    ]
    thinking_events = [event for event in events if isinstance(event, ProviderThinkingDeltaEvent)]
    assert [event.delta for event in thinking_events] == ["trace ", "details"]
    assert isinstance(events[-1], ProviderResponseEndEvent)
    assert events[-1].message.content == "Done"


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
    assert isinstance(events[0], ProviderRetryEvent)
    assert events[0].data == {"status_code": 503, "body": "overloaded"}
    assert [event.type for event in events] == [
        "retry",
        "response_start",
        "text_delta",
        "response_end",
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

    assert isinstance(events[-1], ProviderErrorEvent)
    assert events[-1].message == (
        "anthropic request failed with status 400 for model claude-test: "
        "model: invalid-model is not supported"
    )
    assert events[-1].data == {
        "status_code": 400,
        "body": '{"error":{"message":"model: invalid-model is not supported"}}',
        "attempts": 1,
    }


def _weather_tool() -> AgentTool:
    async def executor(
        arguments: Mapping[str, JSONValue],
        signal: SimpleCancellationToken | None = None,
    ) -> AgentToolResult:
        del signal
        return AgentToolResult(
            tool_call_id="call_1", name="get_weather", ok=True, content=str(arguments)
        )

    return AgentTool(
        name="get_weather",
        description="Get current weather for a city.",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
        executor=executor,
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
            content="",
            tool_calls=[ToolCall(id="call_1", name="get_weather", arguments={"city": "Paris"})],
        ),
        ToolResultMessage(tool_call_id="call_1", name="get_weather", content='{"temp_c": 19}'),
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
        "response_start",
        "text_delta",
        "text_delta",
        "response_end",
    ]
    assert isinstance(events[-1], ProviderResponseEndEvent)
    assert events[-1].message.content == "Sunny"
    assert events[-1].finish_reason == "stop"

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

    tool_call_events = [e for e in events if isinstance(e, ProviderToolCallEvent)]
    assert len(tool_call_events) == 1
    assert tool_call_events[0].tool_call.id == "call_abc"
    assert tool_call_events[0].tool_call.name == "get_weather"
    assert tool_call_events[0].tool_call.arguments == {"city": "Paris"}

    end = events[-1]
    assert isinstance(end, ProviderResponseEndEvent)
    assert len(end.message.tool_calls) == 1
    assert end.message.tool_calls[0].id == "call_abc"
    assert end.message.tool_calls[0].arguments == {"city": "Paris"}
    assert end.finish_reason == "tool_calls"


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
        "response_start",
        "text_delta",
        "text_delta",
        "response_end",
    ]
    text_deltas = [e.delta for e in events if isinstance(e, ProviderTextDeltaEvent)]
    assert text_deltas == ["I can", "not help with that."]
    end = events[-1]
    assert isinstance(end, ProviderResponseEndEvent)
    assert end.message.content == "I cannot help with that."
    assert end.finish_reason == "stop"


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
        "response_start",
        "thinking_delta",
        "text_delta",
        "response_end",
    ]
    thinking = next(e for e in events if isinstance(e, ProviderThinkingDeltaEvent))
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

    error_events = [e for e in events if isinstance(e, ProviderErrorEvent)]
    assert len(error_events) == 1
    assert error_events[0].message == "model exploded"
    # The raw event is preserved for debugging (code/param/type, etc.).
    assert error_events[0].data is not None
    assert error_events[0].data["event"]["type"] == "response.failed"


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
    assert isinstance(end, ProviderResponseEndEvent)
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

    error_events = [e for e in events if isinstance(e, ProviderErrorEvent)]
    assert len(error_events) == 1
    assert error_events[0].message == "rate limited"


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
    assert isinstance(end, ProviderResponseEndEvent)
    assert end.message.content == "partial"
    assert end.finish_reason == "length"
