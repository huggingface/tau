"""Dynamic provider types and source-aware overlay registry for extensions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from os import environ
from typing import Any, Literal, Protocol

from tau_agent.provider import ModelProvider
from tau_coding.provider_catalog import ProviderApi
from tau_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderModelMetadata,
)


class ProviderAuth(Protocol):
    """Authentication resolved at request/runtime creation time."""

    @property
    def mode(self) -> Literal["required", "optional", "none"]: ...

    @property
    def api_key_env(self) -> str: ...

    def resolve(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class RequiredEnvApiKey:
    """Require an API key from an environment variable."""

    env: str
    mode: Literal["required"] = field(default="required", init=False)

    @property
    def api_key_env(self) -> str:
        return self.env

    def resolve(self) -> str:
        value = environ.get(self.env)
        if not value:
            raise RuntimeError(f"Missing API key. Set {self.env} before using this provider.")
        return value


@dataclass(frozen=True, slots=True)
class OptionalEnvApiKey:
    """Use an environment API key when present; otherwise send no auth header."""

    env: str
    mode: Literal["optional"] = field(default="optional", init=False)

    @property
    def api_key_env(self) -> str:
        return self.env

    def resolve(self) -> str | None:
        return environ.get(self.env) or None


@dataclass(frozen=True, slots=True)
class NoAuth:
    """Always omit authentication."""

    mode: Literal["none"] = field(default="none", init=False)

    @property
    def api_key_env(self) -> str:
        # The host config requires a field, but auth="none" never reads it.
        return "TAU_NO_API_KEY"

    def resolve(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class ProviderModel:
    """A model advertised by a dynamic provider; unknown metadata stays ``None``."""

    id: str
    display_name: str | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    input: tuple[Literal["text", "image"], ...] = ()
    reasoning: bool | None = None
    compat: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OpenAICompatibleTransport:
    """Reuse Tau's OpenAI-compatible transport for an extension provider."""

    base_url: str
    api: ProviderApi = "openai-completions"
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = 60.0
    max_retries: int = 2
    max_retry_delay_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class ProviderRefreshContext:
    """Host-owned context passed to asynchronous model discovery."""

    cancelled: asyncio.Event
    network_allowed: bool
    endpoint: str | None
    resolve_auth: Callable[[], str | None]
    source: str

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled.is_set()


ProviderRefresh = Callable[[ProviderRefreshContext], Awaitable[Sequence[ProviderModel]]]
ProviderRuntimeFactory = Callable[[ProviderModel], Awaitable[ModelProvider] | ModelProvider]


@dataclass(frozen=True, slots=True)
class DynamicProvider:
    """Process-local provider registration owned by an extension.

    ``models`` may be empty. ``refresh_models`` owns asynchronous discovery;
    ``runtime_factory`` reserves a provider-neutral runtime seam while v1 ships
    the OpenAI-compatible helper transport.
    """

    id: str
    display_name: str
    transport: OpenAICompatibleTransport
    auth: ProviderAuth = field(default_factory=NoAuth)
    models: tuple[ProviderModel, ...] = ()
    default_model: str | None = None
    refresh_models: ProviderRefresh | None = None
    refresh_timeout_seconds: float | None = None
    runtime_factory: ProviderRuntimeFactory | None = None


@dataclass(frozen=True, slots=True)
class ProviderLayer:
    """One extension-owned definition in a provider overlay stack."""

    source: str
    sequence: int
    provider: DynamicProvider


class DynamicProviderRegistry:
    """Layer provider definitions by stable id and extension ownership."""

    def __init__(self) -> None:
        self._layers: dict[str, list[ProviderLayer]] = {}
        self._next_sequence = 0

    def register(self, source: str, provider: DynamicProvider) -> DynamicProvider:
        """Validate then atomically add/replace the caller's layer."""
        normalized = _validate_provider(provider)
        layers = self._layers.get(normalized.id, [])
        existing = next((layer for layer in layers if layer.source == source), None)
        sequence = existing.sequence if existing is not None else self._next_sequence
        if existing is None:
            self._next_sequence += 1
        replacement = ProviderLayer(source=source, sequence=sequence, provider=normalized)
        updated = [layer for layer in layers if layer.source != source]
        updated.append(replacement)
        updated.sort(key=lambda layer: layer.sequence)
        self._layers[normalized.id] = updated
        return normalized

    def unregister(self, source: str, provider_id: str) -> bool:
        """Remove only the caller's layer, revealing the previous layer."""
        layers = self._layers.get(provider_id)
        if layers is None:
            return False
        updated = [layer for layer in layers if layer.source != source]
        if len(updated) == len(layers):
            return False
        if updated:
            self._layers[provider_id] = updated
        else:
            del self._layers[provider_id]
        return True

    def remove_source(self, source: str) -> None:
        """Remove every layer owned by one failed/unloaded extension."""
        for provider_id in tuple(self._layers):
            self.unregister(source, provider_id)

    def clear(self) -> None:
        self._layers.clear()

    def layer(self, source: str, provider_id: str) -> ProviderLayer | None:
        return next(
            (layer for layer in self._layers.get(provider_id, ()) if layer.source == source),
            None,
        )

    def effective_layer(self, provider_id: str) -> ProviderLayer | None:
        layers = self._layers.get(provider_id, ())
        return layers[-1] if layers else None

    def effective_layers(self) -> tuple[ProviderLayer, ...]:
        return tuple(
            layer
            for provider_id in self._layers
            if (layer := self.effective_layer(provider_id)) is not None
        )

    def provider_ids(self) -> frozenset[str]:
        return frozenset(self._layers)

    def publish_models(
        self,
        source: str,
        provider_id: str,
        models: Sequence[ProviderModel],
    ) -> DynamicProvider:
        """Atomically publish a validated model snapshot for one layer."""
        layer = self.layer(source, provider_id)
        if layer is None:
            raise KeyError(provider_id)
        candidate = replace(layer.provider, models=tuple(models))
        if candidate.default_model not in {model.id for model in candidate.models}:
            candidate = replace(
                candidate,
                default_model=candidate.models[0].id if candidate.models else None,
            )
        # Validate the full host projection before replacing the cached layer.
        provider_to_config(candidate)
        return self.register(source, candidate)


def provider_to_config(provider: DynamicProvider) -> OpenAICompatibleProviderConfig:
    """Project a dynamic provider into Tau's existing OpenAI runtime config."""
    models = tuple(model.id for model in provider.models)
    default_model = provider.default_model or (models[0] if models else "")
    metadata = {
        model.id: ProviderModelMetadata(
            name=model.display_name,
            reasoning=model.reasoning,
            input=model.input,
            context_window=model.context_window,
            max_tokens=model.max_tokens,
            compat=dict(model.compat),
        )
        for model in provider.models
    }
    return OpenAICompatibleProviderConfig(
        name=provider.id,
        base_url=provider.transport.base_url.rstrip("/"),
        api=provider.transport.api,
        api_key_env=provider.auth.api_key_env,
        credential_name=None,
        auth=provider.auth.mode,
        models=models,
        default_model=default_model,
        headers=dict(provider.transport.headers),
        model_metadata=metadata,
        timeout_seconds=provider.transport.timeout_seconds,
        max_retries=provider.transport.max_retries,
        max_retry_delay_seconds=provider.transport.max_retry_delay_seconds,
    )


def _validate_provider(provider: DynamicProvider) -> DynamicProvider:
    provider_id = provider.id.strip()
    display_name = provider.display_name.strip()
    base_url = provider.transport.base_url.strip().rstrip("/")
    if not provider_id:
        raise ValueError("provider id must be non-empty")
    if not display_name:
        raise ValueError("provider display_name must be non-empty")
    if not base_url:
        raise ValueError("OpenAI-compatible provider base_url must be non-empty")
    if provider.refresh_timeout_seconds is not None and provider.refresh_timeout_seconds <= 0:
        raise ValueError("provider refresh_timeout_seconds must be positive")
    if provider.auth.mode != "none" and not provider.auth.api_key_env.strip():
        raise ValueError("provider auth environment variable must be non-empty")
    if provider.runtime_factory is not None and provider.transport is not None:
        # The factory seam is reserved, but an OpenAI transport remains required
        # in v1 so provider settings/model surfaces have one concrete projection.
        pass
    models: list[ProviderModel] = []
    seen: set[str] = set()
    for model in provider.models:
        model_id = model.id.strip()
        if not model_id:
            raise ValueError("provider model id must be non-empty")
        if model_id in seen:
            raise ValueError(f"duplicate provider model id: {model_id}")
        if model.context_window is not None and model.context_window <= 0:
            raise ValueError("provider model context_window must be positive")
        if model.max_tokens is not None and model.max_tokens <= 0:
            raise ValueError("provider model max_tokens must be positive")
        seen.add(model_id)
        models.append(replace(model, id=model_id))
    default_model = provider.default_model
    if default_model is not None and default_model not in seen:
        raise ValueError("provider default_model must be one of its models")
    if default_model is None and models:
        default_model = models[0].id
    return replace(
        provider,
        id=provider_id,
        display_name=display_name,
        transport=replace(provider.transport, base_url=base_url),
        models=tuple(models),
        default_model=default_model,
    )
