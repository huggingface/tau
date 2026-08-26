"""Bounded official-API Ollama adapter spike; not a shipped backend."""

from __future__ import annotations

import json

import httpx2
import pytest

from fixtures.ollama_adapter_spike import OllamaAdapterSpike, context
from tau_coding.extensions import DynamicProviderRegistry, LocalBackendRegistry

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _client() -> tuple[httpx2.AsyncClient, list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def dispatch(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/v1/models":
            return httpx2.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "qwen3:8b", "object": "model", "owned_by": "library"},
                        {"id": "code:latest", "object": "model", "owned_by": "library"},
                    ],
                },
            )
        if request.url.path == "/api/tags":
            return httpx2.Response(
                200,
                json={"models": [{"name": "qwen3:8b"}, {"name": "code:latest"}]},
            )
        if request.url.path == "/api/ps":
            return httpx2.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        raise AssertionError(f"unexpected Ollama spike request: {request.url}")

    return httpx2.AsyncClient(transport=httpx2.MockTransport(dispatch)), requests


async def test_official_ollama_shapes_fit_without_llama_concepts() -> None:
    client, requests = _client()
    adapter = OllamaAdapterSpike(client)
    providers = DynamicProviderRegistry(generation_id="generation")
    backends = LocalBackendRegistry(providers, generation_id="generation")
    providers.register("test:ollama-spike", adapter.provider())
    backends.register("test:ollama-spike", adapter.backend())

    provider_result = await providers.refresh("ollama-spike")
    backend_result = await backends.refresh("ollama-spike")

    assert provider_result.status == "published"
    assert provider_result.provider is not None
    assert [model.id for model in provider_result.provider.models] == [
        "qwen3:8b",
        "code:latest",
    ]
    assert backend_result.backend_status is not None
    assert backend_result.backend_status.models[0].state == "running"
    assert backend_result.backend_status.selected_model == "qwen3:8b"
    assert [request.url.path for request in requests] == [
        "/v1/models",
        "/api/tags",
        "/api/ps",
    ]
    await backends.aclose()
    await providers.aclose()
    await client.aclose()


async def test_ollama_openai_compatibility_uses_no_auth_and_offline_cache() -> None:
    client, requests = _client()
    adapter = OllamaAdapterSpike(client)
    providers = DynamicProviderRegistry(generation_id="generation")
    providers.register("test:ollama-spike", adapter.provider())
    provider_result = await providers.refresh("ollama-spike", allow_network=False)

    assert provider_result.status == "published"
    assert provider_result.provider is not None
    assert provider_result.provider.models == ()
    assert requests == []
    await providers.aclose()
    await client.aclose()


async def test_ollama_spike_rejects_malformed_native_state() -> None:
    async def dispatch(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(200, content=json.dumps({"models": {}}).encode())

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(dispatch))
    adapter = OllamaAdapterSpike(client)
    with pytest.raises(ValueError, match="models list"):
        await adapter.status(context())
    await client.aclose()
