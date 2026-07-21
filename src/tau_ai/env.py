"""Environment-based provider configuration helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from os import environ

from tau_agent.types import JSONValue

DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS = 60.0
DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES = 2
DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class RuntimeProviderAuth:
    """Request auth resolved immediately before a provider call."""

    api_key: str
    base_url: str | None = None
    headers: Mapping[str, str] | None = None


type RuntimeProviderAuthResolver = Callable[[], Awaitable[RuntimeProviderAuth]]


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    """Configuration for an OpenAI-compatible chat completions endpoint."""

    api_key: str
    base_url: str = DEFAULT_OPENAI_COMPATIBLE_BASE_URL
    headers: Mapping[str, str] | None = None
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS
    api: str = "openai-completions"
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    reasoning_effort_parameter: str = "reasoning_effort"
    thinking_format: str = "openai"
    compat: Mapping[str, JSONValue] = field(default_factory=dict)
    include_reasoning_effort_none: bool = False
    provider_name: str = "OpenAI-compatible provider"
    omit_authorization_header: bool = False
    credential_resolver: RuntimeProviderAuthResolver | None = None


@dataclass(frozen=True, slots=True)
class AnthropicConfig:
    """Configuration for Anthropic's Messages API."""

    api_key: str
    bearer_auth: bool = False
    base_url: str = DEFAULT_ANTHROPIC_BASE_URL
    headers: Mapping[str, str] | None = None
    timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES
    max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS
    max_tokens: int | None = None
    thinking_budget_tokens: int | None = None
    thinking_effort: str | None = None
    thinking_mode: str = "budget"
    provider_name: str = "Anthropic"
    oauth_system_prompt: str | None = None
    credential_resolver: RuntimeProviderAuthResolver | None = None


def openai_compatible_config_from_env(
    *,
    api_key_var: str = "OPENAI_API_KEY",
    base_url_var: str = "OPENAI_BASE_URL",
    timeout_seconds_var: str = "OPENAI_TIMEOUT_SECONDS",
    max_retries_var: str = "OPENAI_MAX_RETRIES",
    max_retry_delay_seconds_var: str = "OPENAI_MAX_RETRY_DELAY_SECONDS",
    default_timeout_seconds: float = DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
    default_max_retries: int = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    default_max_retry_delay_seconds: float = DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
) -> OpenAICompatibleConfig:
    """Load OpenAI-compatible provider configuration from environment variables."""
    api_key = environ.get(api_key_var)
    if not api_key:
        msg = f"Missing required environment variable: {api_key_var}"
        raise RuntimeError(msg)

    timeout_seconds = _timeout_seconds_from_env(timeout_seconds_var, default_timeout_seconds)
    max_retries = _non_negative_int_from_env(max_retries_var, default_max_retries)
    max_retry_delay_seconds = _non_negative_float_from_env(
        max_retry_delay_seconds_var, default_max_retry_delay_seconds
    )
    return OpenAICompatibleConfig(
        api_key=api_key,
        base_url=environ.get(base_url_var, DEFAULT_OPENAI_COMPATIBLE_BASE_URL).rstrip("/"),
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        max_retry_delay_seconds=max_retry_delay_seconds,
    )


def _timeout_seconds_from_env(name: str, default: float) -> float:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        timeout_seconds = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable must be a number: {name}") from exc
    if timeout_seconds <= 0:
        raise RuntimeError(f"Environment variable must be greater than 0: {name}")
    return timeout_seconds


def _non_negative_int_from_env(name: str, default: int) -> int:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable must be an integer: {name}") from exc
    if value < 0:
        raise RuntimeError(f"Environment variable must be 0 or greater: {name}")
    return value


def _non_negative_float_from_env(name: str, default: float) -> float:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable must be a number: {name}") from exc
    if value < 0:
        raise RuntimeError(f"Environment variable must be 0 or greater: {name}")
    return value
