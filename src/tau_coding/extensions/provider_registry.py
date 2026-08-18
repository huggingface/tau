"""Generation-local layered registry for extension-defined providers."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from itertools import count
from os import environ
from types import MappingProxyType
from typing import Literal
from uuid import uuid4

from tau_agent.harness import SimpleCancellationToken
from tau_coding.extensions.providers import (
    CredentialReader,
    DynamicProvider,
    ProviderModelSnapshot,
    ProviderRefreshContext,
    resolve_provider_auth,
)
from tau_coding.provider_config import ProviderConfig

DEFAULT_PROVIDER_REFRESH_TIMEOUT_SECONDS = 10.0
MAX_PROVIDER_REFRESH_DIAGNOSTICS = 100


@dataclass(frozen=True, slots=True)
class ProviderLayerToken:
    """Identity required to publish work into one exact source layer."""

    provider_id: str
    source_id: str
    generation_id: str
    layer_id: str


@dataclass(frozen=True, slots=True)
class DynamicProviderLayer:
    """One registered dynamic provider definition and its ownership token."""

    token: ProviderLayerToken
    provider: DynamicProvider
    registration_order: int


@dataclass(frozen=True, slots=True)
class EffectiveProvider:
    """The complete effective provider definition for one provider id."""

    definition: ProviderConfig | DynamicProvider = field(repr=False)
    source_id: str
    layer_token: ProviderLayerToken | None = None

    @property
    def dynamic(self) -> bool:
        """Return whether the effective definition is process-local."""
        return self.layer_token is not None


@dataclass(frozen=True, slots=True)
class ProviderRefreshDiagnostic:
    """Bounded secret-free diagnostic for one failed layer generation."""

    token: ProviderLayerToken
    reason: Literal["cancelled", "failed", "timed_out"]
    message: str


@dataclass(frozen=True, slots=True)
class ProviderRefreshResult:
    """Outcome from one shared refresh operation."""

    status: Literal["published", "unavailable", "cancelled", "failed", "timed_out", "stale"]
    provider: DynamicProvider | None = field(repr=False)
    token: ProviderLayerToken | None


class _NoCredentials:
    def get(self, name: str) -> str | None:
        del name
        return None


class DynamicProviderRegistry:
    """Compose immutable durable baselines with generation-owned dynamic layers.

    The registry is process-local. It has no persistence methods: dynamic
    definitions and refresh snapshots can only live for this registry's runtime
    generation.
    """

    def __init__(
        self,
        durable_providers: Sequence[ProviderConfig] = (),
        *,
        generation_id: str | None = None,
        credentials: CredentialReader | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        durable: dict[str, ProviderConfig] = {}
        for provider in durable_providers:
            if provider.name in durable:
                raise ValueError(f"Duplicate durable provider id: {provider.name}")
            durable[provider.name] = provider
        self._durable = MappingProxyType(durable)
        self._generation_id = generation_id or uuid4().hex
        self._credentials = credentials if credentials is not None else _NoCredentials()
        self._environment = environment if environment is not None else environ
        self._layers: dict[str, list[DynamicProviderLayer]] = {}
        self._order = count(1)
        self._layer_sequence = count(1)
        self._refresh_tasks: dict[ProviderLayerToken, asyncio.Task[ProviderRefreshResult]] = {}
        self._refresh_signals: dict[ProviderLayerToken, SimpleCancellationToken] = {}
        self._diagnostics: dict[ProviderLayerToken, ProviderRefreshDiagnostic] = {}
        self._retired = False

    @property
    def generation_id(self) -> str:
        """Return this staged registry's generation identity."""
        return self._generation_id

    @property
    def durable_providers(self) -> tuple[ProviderConfig, ...]:
        """Return the exact complete durable baseline objects."""
        return tuple(self._durable.values())

    @property
    def diagnostics(self) -> tuple[ProviderRefreshDiagnostic, ...]:
        """Return bounded diagnostics in deterministic insertion order."""
        return tuple(self._diagnostics.values())

    def register(self, source_id: str, provider: DynamicProvider) -> ProviderLayerToken:
        """Atomically register or replace one source's complete provider layer."""
        self._assert_active()
        normalized_source = _source_id(source_id)
        # Provider construction has already performed complete validation. Do
        # not mutate current state before this point.
        token = ProviderLayerToken(
            provider_id=provider.id,
            source_id=normalized_source,
            generation_id=self._generation_id,
            layer_id=f"{self._generation_id}:{next(self._layer_sequence)}",
        )
        layer = DynamicProviderLayer(
            token=token,
            provider=provider,
            registration_order=next(self._order),
        )
        existing = self._layers.get(provider.id, ())
        replaced = [item for item in existing if item.token.source_id == normalized_source]
        self._layers[provider.id] = [
            item for item in existing if item.token.source_id != normalized_source
        ] + [layer]
        for old_layer in replaced:
            self._cancel_token(old_layer.token)
        return token

    def unregister(self, provider_id: str, source_id: str) -> bool:
        """Remove one source layer while preserving all other definitions."""
        normalized_source = _source_id(source_id)
        layers = self._layers.get(provider_id)
        if not layers:
            return False
        removed = [item for item in layers if item.token.source_id == normalized_source]
        if not removed:
            return False
        remaining = [item for item in layers if item.token.source_id != normalized_source]
        if remaining:
            self._layers[provider_id] = remaining
        else:
            self._layers.pop(provider_id, None)
        for layer in removed:
            self._cancel_token(layer.token)
        return True

    def unregister_source(self, source_id: str) -> None:
        """Remove every layer and cancel every task owned by one source."""
        normalized_source = _source_id(source_id)
        for provider_id in tuple(self._layers):
            self.unregister(provider_id, normalized_source)

    def effective(self, provider_id: str) -> EffectiveProvider | None:
        """Return the latest complete active layer, then the durable baseline."""
        layers = self._layers.get(provider_id)
        if layers:
            latest = layers[-1]
            return EffectiveProvider(
                definition=latest.provider,
                source_id=latest.token.source_id,
                layer_token=latest.token,
            )
        durable = self._durable.get(provider_id)
        if durable is None:
            return None
        return EffectiveProvider(definition=durable, source_id="durable")

    def effective_providers(self) -> tuple[EffectiveProvider, ...]:
        """Return a deterministic composed view without modifying durable settings."""
        ids = list(self._durable)
        ids.extend(provider_id for provider_id in self._layers if provider_id not in self._durable)
        return tuple(
            effective
            for provider_id in ids
            if (effective := self.effective(provider_id)) is not None
        )

    def layers(self, provider_id: str) -> tuple[DynamicProviderLayer, ...]:
        """Return active dynamic layers in precedence order."""
        return tuple(self._layers.get(provider_id, ()))

    async def refresh(
        self,
        provider_id: str,
        *,
        allow_network: bool = True,
        timeout_seconds: float = DEFAULT_PROVIDER_REFRESH_TIMEOUT_SECONDS,
    ) -> ProviderRefreshResult:
        """Refresh the effective dynamic layer, coalescing concurrent callers.

        Caller cancellation does not cancel shared work; generation retirement,
        source removal, replacement, or :meth:`cancel_refresh` owns cancellation.
        """
        if (
            not isinstance(timeout_seconds, int | float)
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("Provider refresh timeout must be greater than 0")
        effective = self.effective(provider_id)
        if (
            self._retired
            or effective is None
            or not isinstance(effective.definition, DynamicProvider)
            or effective.layer_token is None
            or effective.definition.refresh_models is None
        ):
            provider = (
                effective.definition
                if effective is not None and isinstance(effective.definition, DynamicProvider)
                else None
            )
            return ProviderRefreshResult(
                "unavailable",
                provider,
                effective.layer_token if effective else None,
            )

        token = effective.layer_token
        task = self._refresh_tasks.get(token)
        if task is None:
            signal = SimpleCancellationToken()
            self._refresh_signals[token] = signal
            task = asyncio.create_task(
                self._run_refresh(
                    token,
                    effective.definition,
                    signal,
                    allow_network=allow_network,
                    timeout_seconds=timeout_seconds,
                ),
                name=f"tau-provider-refresh:{token.source_id}:{provider_id}",
            )
            self._refresh_tasks[token] = task
            task.add_done_callback(partial(self._finish_refresh, token))
        return await asyncio.shield(task)

    def cancel_refresh(self, provider_id: str, source_id: str | None = None) -> bool:
        """Cancel refresh work for matching active provider layers."""
        cancelled = False
        for layer in self._layers.get(provider_id, ()):
            if source_id is None or layer.token.source_id == source_id:
                cancelled = self._cancel_token(layer.token) or cancelled
        return cancelled

    def retire(self) -> None:
        """Synchronously invalidate this generation and cancel all owned work."""
        if self._retired:
            return
        self._retired = True
        for token in tuple(self._refresh_tasks):
            self._cancel_token(token)
        self._refresh_tasks.clear()
        self._refresh_signals.clear()
        self._layers.clear()

    async def aclose(self) -> None:
        """Retire and await cancellation of all owned refresh tasks."""
        tasks = tuple(self._refresh_tasks.values())
        self.retire()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_refresh(
        self,
        token: ProviderLayerToken,
        provider: DynamicProvider,
        signal: SimpleCancellationToken,
        *,
        allow_network: bool,
        timeout_seconds: float,
    ) -> ProviderRefreshResult:
        callback_task: asyncio.Task[ProviderModelSnapshot] | None = None
        try:
            callback_task = asyncio.create_task(
                self._discover_snapshot(
                    provider,
                    signal,
                    allow_network=allow_network,
                ),
                name=f"tau-provider-discovery:{token.source_id}:{token.provider_id}",
            )
            done, _ = await asyncio.wait((callback_task,), timeout=timeout_seconds)
            if not done:
                signal.cancel()
                callback_task.cancel()
                callback_task.add_done_callback(_consume_task_result)
                self._record_diagnostic(
                    token,
                    "timed_out",
                    "provider model refresh timed out",
                )
                return ProviderRefreshResult("timed_out", self._current_provider(token), token)
            snapshot = callback_task.result()
            if not isinstance(snapshot, ProviderModelSnapshot):
                raise TypeError("refresh must return ProviderModelSnapshot")
            candidate = provider.with_snapshot(snapshot)
        except asyncio.CancelledError:
            signal.cancel()
            if callback_task is not None and not callback_task.done():
                callback_task.cancel()
                callback_task.add_done_callback(_consume_task_result)
            self._record_diagnostic(token, "cancelled", "provider model refresh was cancelled")
            return ProviderRefreshResult("cancelled", self._current_provider(token), token)
        except Exception:
            # Arbitrary extension exception text may contain request data or a
            # secret. Keep diagnostics categorical and never include repr(exc).
            self._record_diagnostic(token, "failed", "provider model refresh failed")
            return ProviderRefreshResult("failed", self._current_provider(token), token)

        if signal.is_cancelled() or not self._token_is_current(token):
            return ProviderRefreshResult("stale", self._current_provider(token), token)
        layers = self._layers[token.provider_id]
        self._layers[token.provider_id] = [
            DynamicProviderLayer(
                token=layer.token,
                provider=candidate,
                registration_order=layer.registration_order,
            )
            if layer.token == token
            else layer
            for layer in layers
        ]
        return ProviderRefreshResult("published", candidate, token)

    async def _discover_snapshot(
        self,
        provider: DynamicProvider,
        signal: SimpleCancellationToken,
        *,
        allow_network: bool,
    ) -> ProviderModelSnapshot:
        auth = await resolve_provider_auth(
            provider.auth,
            credentials=self._credentials,
            environment=self._environment,
        )
        callback = provider.refresh_models
        assert callback is not None
        context = ProviderRefreshContext(
            signal=signal,
            allow_network=allow_network,
            cached_models=provider.models,
            auth=auth,
        )
        return await _await_snapshot(callback(context))

    def _token_is_current(self, token: ProviderLayerToken) -> bool:
        if self._retired or token.generation_id != self._generation_id:
            return False
        return any(layer.token == token for layer in self._layers.get(token.provider_id, ()))

    def _current_provider(self, token: ProviderLayerToken) -> DynamicProvider | None:
        for layer in self._layers.get(token.provider_id, ()):
            if layer.token == token:
                return layer.provider
        return None

    def _cancel_token(self, token: ProviderLayerToken) -> bool:
        signal = self._refresh_signals.pop(token, None)
        task = self._refresh_tasks.pop(token, None)
        if signal is not None:
            signal.cancel()
        if task is not None and not task.done():
            task.cancel()
            return True
        return signal is not None

    def _finish_refresh(
        self,
        token: ProviderLayerToken,
        task: asyncio.Task[ProviderRefreshResult],
    ) -> None:
        if self._refresh_tasks.get(token) is task:
            self._refresh_tasks.pop(token, None)
            self._refresh_signals.pop(token, None)

    def _record_diagnostic(
        self,
        token: ProviderLayerToken,
        reason: Literal["cancelled", "failed", "timed_out"],
        message: str,
    ) -> None:
        if token in self._diagnostics:
            return
        if len(self._diagnostics) >= MAX_PROVIDER_REFRESH_DIAGNOSTICS:
            oldest = next(iter(self._diagnostics))
            self._diagnostics.pop(oldest)
        self._diagnostics[token] = ProviderRefreshDiagnostic(token, reason, message)

    def _assert_active(self) -> None:
        if self._retired:
            raise RuntimeError("Dynamic provider registry generation is retired")


async def _await_snapshot(
    value: Awaitable[ProviderModelSnapshot],
) -> ProviderModelSnapshot:
    return await value


def _consume_task_result(task: asyncio.Task[ProviderModelSnapshot]) -> None:
    """Retrieve a detached cancelled discovery result without publishing it."""
    with suppress(asyncio.CancelledError):
        task.exception()


def _source_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Provider source id must be a non-empty string")
    normalized = value.strip()
    if normalized != value:
        raise ValueError("Provider source id must not have surrounding whitespace")
    return normalized
