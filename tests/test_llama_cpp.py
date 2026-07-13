"""Tests for Tau's first-class llama.cpp integration."""

from __future__ import annotations

from json import loads

import httpx
import pytest

from tau_coding.llama_cpp import (
    LlamaCppError,
    configured_llama_cpp_provider,
    diagnose_llama_cpp,
    discover_llama_cpp,
    normalize_llama_cpp_base_url,
)
from tau_coding.provider_config import OpenAICompatibleProviderConfig


def test_normalize_llama_cpp_base_url_adds_v1() -> None:
    assert normalize_llama_cpp_base_url("http://127.0.0.1:8080/") == ("http://127.0.0.1:8080/v1")
    assert normalize_llama_cpp_base_url("http://127.0.0.1:8080/v1/") == ("http://127.0.0.1:8080/v1")


@pytest.mark.anyio
async def test_discover_llama_cpp_models_without_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"object": "list", "data": [{"id": "qwen.gguf"}, {"id": "coder.gguf"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        info = await discover_llama_cpp("http://localhost:8080", client=client)

    assert info.base_url == "http://localhost:8080/v1"
    assert info.models == ("qwen.gguf", "coder.gguf")
    assert requests[0].url == "http://localhost:8080/v1/models"
    assert "authorization" not in requests[0].headers


@pytest.mark.anyio
async def test_discover_llama_cpp_uses_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await discover_llama_cpp("http://localhost:8080/v1", api_key="secret", client=client)


@pytest.mark.anyio
async def test_discover_llama_cpp_explains_auth_failure() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(401))
    ) as client:
        with pytest.raises(LlamaCppError, match="LLAMA_API_KEY"):
            await discover_llama_cpp("http://localhost:8080", client=client)


def test_configured_llama_cpp_provider_uses_discovered_models() -> None:
    from tau_coding.llama_cpp import LlamaCppServerInfo

    provider = configured_llama_cpp_provider(
        OpenAICompatibleProviderConfig(
            name="llama-cpp",
            auth="optional",
            model_discovery="openai",
        ),
        LlamaCppServerInfo(
            base_url="http://localhost:8080/v1",
            models=("qwen.gguf", "coder.gguf"),
        ),
        model="coder.gguf",
    )

    assert provider.models == ("qwen.gguf", "coder.gguf")
    assert provider.default_model == "coder.gguf"


@pytest.mark.anyio
async def test_diagnose_llama_cpp_checks_streaming_and_tools() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen.gguf"}]})
        payload = loads(request.content)
        if payload.get("tools"):
            body = (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
                '"function":{"name":"tau_probe","arguments":"{}"}}]},'
                '"finish_reason":"tool_calls"}]}\n\ndata: [DONE]\n\n'
            )
        else:
            body = (
                'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    provider = OpenAICompatibleProviderConfig(
        name="llama-cpp",
        base_url="http://localhost:8080/v1",
        auth="optional",
        model_discovery="openai",
        models=("qwen.gguf",),
        default_model="qwen.gguf",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await diagnose_llama_cpp(provider, client=client)

    assert report.ok
    assert [item.status for item in report.diagnostics] == ["ok", "ok", "ok", "ok"]
    assert len(requests) == 3
    chat_payload = loads(requests[1].content)
    assert "store" not in chat_payload
    assert "stream_options" not in chat_payload
