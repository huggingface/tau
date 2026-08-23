"""Frontend-free contracts for process-local extension providers."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from inspect import isawaitable
from types import MappingProxyType
from typing import Protocol

import httpx

from tau_agent.provider import CancellationToken, ModelProvider
from tau_agent.types import JSONPrimitive, JSONValue
from tau_ai.env import (
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
)
from tau_coding.provider_catalog import ModelInput, ProviderApi
from tau_coding.thinking import ThinkingLevel

_PROVIDER_APIS = frozenset(
    {
        "openai-completions",
        "openai-responses",
        "anthropic-messages",
        "openai-codex-responses",
        "google-generative-ai",
        "mistral-conversations",
    }
)
_THINKING_LEVELS = frozenset({"off", "minimal", "low", "medium", "high", "xhigh"})

type ModelCost = Mapping[str, float]
type ImmutableJSONValue = (
    JSONPrimitive | tuple[ImmutableJSONValue, ...] | Mapping[str, ImmutableJSONValue]
)
type ModelCompat = Mapping[str, JSONValue | ImmutableJSONValue]


class DynamicProviderError(ValueError):
    """Raised when a dynamic provider contract is invalid."""


class ProviderAuthError(RuntimeError):
    """Raised when required provider authentication cannot be resolved."""


class _MissingRequiredApiKeyError(ProviderAuthError):
    """Host-authored missing-key guidance safe to preserve at runtime boundaries."""


class CredentialReader(Protocol):
    """Read-only credential lookup used by dynamic auth strategies."""

    def get(self, name: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ProviderAuthContext:
    """Secret-bearing inputs available only while resolving provider auth."""

    credentials: CredentialReader = field(repr=False)
    environment: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ResolvedProviderAuth:
    """Authentication resolved immediately before refresh or runtime creation.

    Secret-bearing fields are deliberately omitted from ``repr``. This value is
    runtime-only and has no serialization helper.
    """

    api_key: str | None = field(default=None, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    source: str = field(default="none", repr=False)
    omit_authorization_header: bool = True

    def __post_init__(self) -> None:
        if self.api_key is not None and (
            not isinstance(self.api_key, str) or not self.api_key.strip()
        ):
            raise ProviderAuthError("Resolved provider API key must be a non-empty string or None")
        if not isinstance(self.omit_authorization_header, bool):
            raise ProviderAuthError("Authorization omission flag must be a boolean")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ProviderAuthError("Authentication source must be a non-empty string")
        headers = _string_mapping(self.headers, "Auth headers")
        has_authorization = _has_header(headers, "authorization")
        if self.api_key is None and not self.omit_authorization_header and not has_authorization:
            raise ProviderAuthError(
                "Resolved provider auth must supply an API key or Authorization header"
            )
        object.__setattr__(self, "headers", headers)


class ProviderAuth(Protocol):
    """Resolve dynamic provider authentication without persisting it."""

    async def resolve(self, context: ProviderAuthContext) -> ResolvedProviderAuth: ...


@dataclass(frozen=True, slots=True)
class RequiredApiKey:
    """Resolve a required API key from Tau credentials, then the environment."""

    credential_name: str
    env_var: str

    def __post_init__(self) -> None:
        _require_non_empty(self.credential_name, "Credential name")
        _require_non_empty(self.env_var, "Environment variable")

    async def resolve(self, context: ProviderAuthContext) -> ResolvedProviderAuth:
        stored = context.credentials.get(self.credential_name)
        if stored:
            return ResolvedProviderAuth(
                api_key=stored,
                source="stored credential",
                omit_authorization_header=False,
            )
        environment_value = context.environment.get(self.env_var)
        if environment_value:
            return ResolvedProviderAuth(
                api_key=environment_value,
                source=f"environment variable {self.env_var}",
                omit_authorization_header=False,
            )
        raise _MissingRequiredApiKeyError(
            f"Missing required authentication. Store credential `{self.credential_name}` "
            f"or set {self.env_var}."
        )


@dataclass(frozen=True, slots=True)
class OptionalApiKey:
    """Resolve an optional API key, omitting Authorization when absent."""

    credential_name: str
    env_var: str

    def __post_init__(self) -> None:
        _require_non_empty(self.credential_name, "Credential name")
        _require_non_empty(self.env_var, "Environment variable")

    async def resolve(self, context: ProviderAuthContext) -> ResolvedProviderAuth:
        stored = context.credentials.get(self.credential_name)
        if stored:
            return ResolvedProviderAuth(
                api_key=stored,
                source="stored credential",
                omit_authorization_header=False,
            )
        environment_value = context.environment.get(self.env_var)
        if environment_value:
            return ResolvedProviderAuth(
                api_key=environment_value,
                source=f"environment variable {self.env_var}",
                omit_authorization_header=False,
            )
        return ResolvedProviderAuth()


@dataclass(frozen=True, slots=True)
class NoAuth:
    """Authentication strategy that never reads credentials or environment."""

    async def resolve(self, context: ProviderAuthContext) -> ResolvedProviderAuth:
        del context
        return ResolvedProviderAuth()


@dataclass(frozen=True, slots=True)
class ProviderModel:
    """One model in a complete dynamic provider snapshot.

    Unknown metadata remains ``None``. Runtime headers are never represented in
    ``repr`` and this type intentionally has no generic persistence method.
    """

    id: str
    display_name: str | None = None
    api: ProviderApi | None = None
    base_url: str | None = None
    reasoning: bool | None = None
    input_modalities: tuple[ModelInput, ...] | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    cost: ModelCost | None = None
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    compat: ModelCompat = field(default_factory=dict)
    thinking_levels: tuple[ThinkingLevel, ...] | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.id, "Model id")
        if self.display_name is not None:
            _require_non_empty(self.display_name, "Model display name")
        if self.api is not None and self.api not in _PROVIDER_APIS:
            raise DynamicProviderError("Model api is unsupported")
        if self.base_url is not None:
            _require_non_empty(self.base_url, "Model base URL")
        if self.reasoning is not None and not isinstance(self.reasoning, bool):
            raise DynamicProviderError("Model reasoning must be a boolean or None")
        if self.input_modalities is not None:
            if not isinstance(self.input_modalities, tuple):
                raise DynamicProviderError("Model input modalities must be a tuple or None")
            if len(set(self.input_modalities)) != len(self.input_modalities):
                raise DynamicProviderError("Model input modalities must not contain duplicates")
            if any(value not in {"text", "image"} for value in self.input_modalities):
                raise DynamicProviderError("Model input modalities must be text or image")
        _validate_optional_positive_int(self.context_window, "Model context window")
        _validate_optional_positive_int(self.max_tokens, "Model max tokens")
        if self.cost is not None:
            object.__setattr__(self, "cost", _cost_mapping(self.cost))
        headers = _string_mapping(self.headers, "Model headers")
        _reject_authorization_header(headers, "Model headers")
        object.__setattr__(self, "headers", headers)
        object.__setattr__(self, "compat", _json_mapping(self.compat, "Model compat"))
        if self.thinking_levels is not None:
            if not isinstance(self.thinking_levels, tuple):
                raise DynamicProviderError("Model thinking levels must be a tuple or None")
            if any(level not in _THINKING_LEVELS for level in self.thinking_levels):
                raise DynamicProviderError("Model thinking level is unsupported")
            if len(set(self.thinking_levels)) != len(self.thinking_levels):
                raise DynamicProviderError("Model thinking levels must not contain duplicates")


@dataclass(frozen=True, slots=True)
class ProviderModelSnapshot:
    """Complete atomic candidate model snapshot returned by discovery."""

    models: tuple[ProviderModel, ...] = ()
    default_model: str | None = None

    def __post_init__(self) -> None:
        _validate_model_snapshot(self.models, self.default_model)


@dataclass(frozen=True, slots=True)
class OpenAICompatibleTransport:
    """Descriptor for Tau's existing OpenAI-compatible streaming transport.

    ``client`` is an optional externally owned client seam for trusted adapters
    and deterministic tests. Providers never close it; the caller owns its
    lifetime. Normal transports leave it unset and use Tau's standard client.
    """

    base_url: str
    api: ProviderApi = "openai-completions"
    auth: ProviderAuth = field(default_factory=NoAuth, repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS
    client: httpx.AsyncClient | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_non_empty(self.base_url, "Transport base URL")
        if not callable(getattr(self.auth, "resolve", None)):
            raise DynamicProviderError("Transport auth must define resolve(context)")
        if self.api not in {"openai-completions", "openai-responses"}:
            raise DynamicProviderError(
                "OpenAI-compatible transport api must be openai-completions or openai-responses"
            )
        headers = _string_mapping(self.headers, "Transport headers")
        _reject_authorization_header(headers, "Transport headers")
        object.__setattr__(self, "headers", headers)
        if (
            not isinstance(self.timeout_seconds, int | float)
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise DynamicProviderError("Transport timeout must be greater than 0")
        if (
            not isinstance(self.max_retries, int)
            or isinstance(self.max_retries, bool)
            or self.max_retries < 0
        ):
            raise DynamicProviderError("Transport max retries must be 0 or greater")
        if (
            not isinstance(self.max_retry_delay_seconds, int | float)
            or isinstance(self.max_retry_delay_seconds, bool)
            or not math.isfinite(self.max_retry_delay_seconds)
            or self.max_retry_delay_seconds < 0
        ):
            raise DynamicProviderError("Transport max retry delay must be 0 or greater")


@dataclass(frozen=True, slots=True)
class ProviderRuntimeContext:
    """Frontend-independent inputs passed to a custom runtime factory."""

    provider_id: str
    auth: ResolvedProviderAuth = field(repr=False)


class ClosableModelProvider(ModelProvider, Protocol):
    """Runtime provider returned by a dynamic provider factory."""

    async def aclose(self) -> None: ...


RuntimeFactory = Callable[
    [ProviderRuntimeContext, ProviderModel],
    Awaitable[ClosableModelProvider] | ClosableModelProvider,
]


@dataclass(frozen=True, slots=True)
class ProviderRefreshContext:
    """Inputs for one bounded, generation-owned discovery operation."""

    signal: CancellationToken
    allow_network: bool
    cached_models: tuple[ProviderModel, ...]
    auth: ResolvedProviderAuth = field(repr=False)


RefreshModels = Callable[[ProviderRefreshContext], Awaitable[ProviderModelSnapshot]]


@dataclass(frozen=True, slots=True)
class DynamicProvider:
    """A complete process-local provider definition owned by one source layer.

    ``stable_scoped_references`` is an opt-in reserved for trusted built-in
    providers with a stable identity: it lets the host persist scoped-model
    references as ``provider id + exact model id`` pairs only.  Definitions,
    endpoints, credentials, and discovered metadata are never persisted, and
    references resolve only while this trusted source is loaded and its live
    or safely cached snapshot contains the model.
    """

    id: str
    display_name: str
    models: tuple[ProviderModel, ...] = ()
    default_model: str | None = None
    transport: OpenAICompatibleTransport | None = None
    runtime_factory: RuntimeFactory | None = field(default=None, repr=False)
    runtime_auth: ProviderAuth = field(default_factory=NoAuth, repr=False)
    refresh_models: RefreshModels | None = field(default=None, repr=False)
    stable_scoped_references: bool = False

    def __post_init__(self) -> None:
        _require_identifier(self.id, "Provider id")
        _require_non_empty(self.display_name, "Provider display name")
        _validate_model_snapshot(self.models, self.default_model)
        if (self.transport is None) == (self.runtime_factory is None):
            raise DynamicProviderError(
                "Dynamic provider must define exactly one transport or runtime factory"
            )
        if self.transport is not None and not isinstance(self.transport, OpenAICompatibleTransport):
            raise DynamicProviderError("Dynamic provider transport is unsupported")
        if self.transport is not None and any(
            model.api not in {None, "openai-completions", "openai-responses"}
            for model in self.models
        ):
            raise DynamicProviderError(
                "OpenAI-compatible provider models must use an OpenAI-compatible api"
            )
        if self.runtime_factory is not None and not callable(self.runtime_factory):
            raise DynamicProviderError("Dynamic provider runtime factory must be callable")
        if self.runtime_factory is not None and not callable(
            getattr(self.runtime_auth, "resolve", None)
        ):
            raise DynamicProviderError("Runtime factory auth must define resolve(context)")
        if self.refresh_models is not None and not callable(self.refresh_models):
            raise DynamicProviderError("Dynamic provider refresh callback must be callable")
        if not isinstance(self.stable_scoped_references, bool):
            raise DynamicProviderError(
                "Dynamic provider stable scoped references flag must be boolean"
            )

    @property
    def auth(self) -> ProviderAuth:
        """Return the auth strategy for refresh and runtime creation."""
        if self.transport is not None:
            return self.transport.auth
        return self.runtime_auth

    def with_snapshot(self, snapshot: ProviderModelSnapshot) -> DynamicProvider:
        """Return this definition with one validated complete snapshot."""
        return DynamicProvider(
            id=self.id,
            display_name=self.display_name,
            models=snapshot.models,
            default_model=snapshot.default_model,
            transport=self.transport,
            runtime_factory=self.runtime_factory,
            runtime_auth=self.runtime_auth,
            refresh_models=self.refresh_models,
            stable_scoped_references=self.stable_scoped_references,
        )


async def resolve_provider_auth(
    auth: ProviderAuth,
    *,
    credentials: CredentialReader,
    environment: Mapping[str, str],
) -> ResolvedProviderAuth:
    """Resolve one auth strategy and validate its runtime-only result."""
    resolved = auth.resolve(ProviderAuthContext(credentials=credentials, environment=environment))
    value = await resolved if isawaitable(resolved) else resolved
    if not isinstance(value, ResolvedProviderAuth):
        raise ProviderAuthError("Provider auth returned an unsupported result")
    return value


def _validate_model_snapshot(
    models: tuple[ProviderModel, ...],
    default_model: str | None,
) -> None:
    if not isinstance(models, tuple) or any(
        not isinstance(model, ProviderModel) for model in models
    ):
        raise DynamicProviderError("Provider models must be a tuple of ProviderModel values")
    ids = [model.id for model in models]
    if len(ids) != len(set(ids)):
        raise DynamicProviderError("Provider model ids must be unique")
    if default_model is not None:
        _require_identifier(default_model, "Default model")
        if default_model not in ids:
            raise DynamicProviderError("Default model must belong to the provider snapshot")


def _require_identifier(value: str, label: str) -> None:
    _require_non_empty(value, label)
    if value != value.strip():
        raise DynamicProviderError(f"{label} must not have surrounding whitespace")


def _require_non_empty(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DynamicProviderError(f"{label} must be a non-empty string")


def _validate_optional_positive_int(value: int | None, label: str) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
        raise DynamicProviderError(f"{label} must be a positive integer")


def _string_mapping(value: Mapping[str, str], label: str) -> Mapping[str, str]:
    copied: dict[str, str] = {}
    normalized_keys: set[str] = set()
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(item, str):
            raise DynamicProviderError(f"{label} must map non-empty strings to strings")
        normalized_key = key.casefold()
        if normalized_key in normalized_keys:
            raise DynamicProviderError(f"{label} must not contain duplicate header names")
        normalized_keys.add(normalized_key)
        copied[key] = item
    return MappingProxyType(copied)


def _has_header(headers: Mapping[str, str], name: str) -> bool:
    normalized = name.casefold()
    return any(key.casefold() == normalized for key in headers)


def _reject_authorization_header(headers: Mapping[str, str], label: str) -> None:
    if _has_header(headers, "authorization"):
        raise DynamicProviderError(
            f"{label} must resolve Authorization through the provider auth strategy"
        )


def _cost_mapping(value: Mapping[str, float]) -> Mapping[str, float]:
    copied: dict[str, float] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(item, int | float)
            or isinstance(item, bool)
            or not math.isfinite(item)
            or item < 0
        ):
            raise DynamicProviderError("Model cost must map non-empty strings to finite numbers")
        copied[key] = float(item)
    return MappingProxyType(copied)


def json_compatible_mapping(value: ModelCompat) -> dict[str, JSONValue]:
    """Return a mutable JSON-compatible copy of deeply frozen metadata."""
    return {key: _copy_mutable_json(item) for key, item in value.items()}


def _json_mapping(value: ModelCompat, label: str) -> ModelCompat:
    copied: dict[str, ImmutableJSONValue] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise DynamicProviderError(f"{label} keys must be non-empty strings")
        copied[key] = _copy_json(item, label)
    return MappingProxyType(copied)


def _copy_json(value: JSONValue | ImmutableJSONValue, label: str) -> ImmutableJSONValue:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DynamicProviderError(f"{label} must contain finite JSON numbers")
        return value
    if isinstance(value, list | tuple):
        return tuple(_copy_json(item, label) for item in value)
    if isinstance(value, Mapping):
        copied: dict[str, ImmutableJSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DynamicProviderError(f"{label} object keys must be strings")
            copied[key] = _copy_json(item, label)
        return MappingProxyType(copied)
    raise DynamicProviderError(f"{label} must contain only JSON values")


def _copy_mutable_json(value: JSONValue | ImmutableJSONValue) -> JSONValue:
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, list | tuple):
        return [_copy_mutable_json(item) for item in value]
    return {key: _copy_mutable_json(item) for key, item in value.items()}
