"""Non-shipping Ollama adapter spike for Phase 6 contract validation.

This file deliberately lives under tests: it is an executable contract probe,
not a shipped Ollama integration. The endpoint shapes come from Ollama's
current official API documentation; the test supplies deterministic responses.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import httpx2

from tau_agent.harness import SimpleCancellationToken
from tau_coding.extensions import (
    DynamicProvider,
    LocalBackend,
    LocalBackendStatus,
    LocalConfigureResult,
    LocalConfigureSpec,
    LocalConfigValues,
    LocalModel,
    LocalOperationContext,
    LocalOperationResult,
    NoAuth,
    OpenAICompatibleTransport,
    ProviderModel,
    ProviderModelSnapshot,
)


@dataclass(slots=True)
class OllamaAdapterSpike:
    """Small adapter exercising only generic provider/backend seams."""

    client: httpx2.AsyncClient
    endpoint: str = "http://ollama.test:11434"

    def __post_init__(self) -> None:
        self.endpoint = self.endpoint.rstrip("/")

    def provider(self) -> DynamicProvider:
        return DynamicProvider(
            id="ollama-spike",
            display_name="Ollama (spike)",
            transport=OpenAICompatibleTransport(
                base_url=f"{self.endpoint}/v1",
                auth=NoAuth(),
                client=self.client,
            ),
            refresh_models=self.refresh_models,
        )

    def backend(self) -> LocalBackend:
        return LocalBackend(
            id="ollama-spike",
            provider_id="ollama-spike",
            display_name="Ollama (spike)",
            configure_spec=LocalConfigureSpec(),
            configure=self.configure,
            status=self.status,
            refresh=self.refresh,
        )

    async def refresh_models(self, context) -> ProviderModelSnapshot:
        if not context.allow_network:
            return ProviderModelSnapshot(context.cached_models)
        response = await self.client.get(f"{self.endpoint}/v1/models")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, list):
            raise ValueError("Ollama /v1/models response has no data list")
        models = tuple(
            ProviderModel(
                id=item["id"],
                display_name=item.get("id"),
                api="openai-completions",
                compat={
                    key: item[key]
                    for key in ("object", "owned_by")
                    if isinstance(item.get(key), str)
                },
            )
            for item in data
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        )
        return ProviderModelSnapshot(models, models[0].id if len(models) == 1 else None)

    async def configure(
        self,
        values: LocalConfigValues,
        context: LocalOperationContext,
    ) -> LocalConfigureResult:
        del values, context
        return LocalConfigureResult(committed=True)

    async def status(
        self, context: LocalOperationContext
    ) -> LocalBackendStatus | LocalOperationResult:
        del context
        tags_response, running_response = await self._get_model_state()
        installed = _models_from_tags(tags_response)
        running = _running_ids(running_response)
        models = tuple(
            LocalModel(model_id, state="running" if model_id in running else "installed")
            for model_id in installed
        )
        selected = next(iter(running), None)
        return LocalBackendStatus(
            state="ready",
            endpoint_display=f"{self.endpoint}/v1",
            models=models,
            selected_model=selected if selected in {item.id for item in models} else None,
            actions=("configure", "refresh", "use") if models else ("configure", "refresh"),
        )

    async def refresh(
        self, context: LocalOperationContext
    ) -> LocalBackendStatus | LocalOperationResult:
        return await self.status(context)

    async def _get_model_state(self) -> tuple[Mapping[str, object], Mapping[str, object]]:
        tags = await self.client.get(f"{self.endpoint}/api/tags")
        tags.raise_for_status()
        running = await self.client.get(f"{self.endpoint}/api/ps")
        running.raise_for_status()
        tags_payload = tags.json()
        running_payload = running.json()
        if not isinstance(tags_payload, Mapping) or not isinstance(running_payload, Mapping):
            raise ValueError("Ollama model state response is malformed")
        return tags_payload, running_payload


def _models_from_tags(payload: Mapping[str, object]) -> tuple[str, ...]:
    models = payload.get("models")
    if not isinstance(models, list):
        raise ValueError("Ollama /api/tags response has no models list")
    result = tuple(
        item.get("name")
        for item in models
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    )
    return tuple(model for model in result if model)


def _running_ids(payload: Mapping[str, object]) -> frozenset[str]:
    models = payload.get("models")
    if not isinstance(models, list):
        raise ValueError("Ollama /api/ps response has no models list")
    return frozenset(
        item.get("name")
        for item in models
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    )


def context() -> LocalOperationContext:
    """Return a deterministic operation context for direct adapter probes."""
    return LocalOperationContext(
        signal=SimpleCancellationToken(),
        action="refresh",
        generation_id="ollama-spike",
        backend_id="ollama-spike",
        source_id="test:ollama-spike",
        _is_current=lambda: True,
        _progress=lambda _: None,
    )
