"""Provider and model streaming layer for Tau."""

from __future__ import annotations

from tau_ai.anthropic import AnthropicProvider
from tau_ai.env import (
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES,
    DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS,
    DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS,
    AnthropicConfig,
    OpenAICompatibleConfig,
    RuntimeProviderAuth,
    openai_compatible_config_from_env,
)
from tau_ai.events import (
    ProviderErrorEvent,
    ProviderEvent,
    ProviderResponseEndEvent,
    ProviderResponseStartEvent,
    ProviderRetryEvent,
    ProviderTextDeltaEvent,
    ProviderThinkingDeltaEvent,
    ProviderToolCallEvent,
)
from tau_ai.fake import FakeProvider
from tau_ai.google import GoogleGenerativeAIProvider
from tau_ai.mistral import MistralConversationsProvider
from tau_ai.openai_codex import (
    DEFAULT_OPENAI_CODEX_BASE_URL,
    OpenAICodexConfig,
    OpenAICodexCredentials,
    OpenAICodexProvider,
)
from tau_ai.openai_compatible import OpenAICompatibleProvider
from tau_ai.provider import CancellationToken, ModelProvider

__all__ = [
    "CancellationToken",
    "AnthropicConfig",
    "AnthropicProvider",
    "DEFAULT_ANTHROPIC_BASE_URL",
    "DEFAULT_OPENAI_COMPATIBLE_MAX_RETRIES",
    "DEFAULT_OPENAI_COMPATIBLE_MAX_RETRY_DELAY_SECONDS",
    "DEFAULT_OPENAI_COMPATIBLE_TIMEOUT_SECONDS",
    "DEFAULT_OPENAI_CODEX_BASE_URL",
    "FakeProvider",
    "GoogleGenerativeAIProvider",
    "MistralConversationsProvider",
    "ModelProvider",
    "OpenAICodexConfig",
    "OpenAICodexCredentials",
    "OpenAICodexProvider",
    "OpenAICompatibleConfig",
    "OpenAICompatibleProvider",
    "RuntimeProviderAuth",
    "ProviderErrorEvent",
    "ProviderEvent",
    "ProviderResponseEndEvent",
    "ProviderResponseStartEvent",
    "ProviderRetryEvent",
    "ProviderThinkingDeltaEvent",
    "ProviderTextDeltaEvent",
    "ProviderToolCallEvent",
    "openai_compatible_config_from_env",
]
