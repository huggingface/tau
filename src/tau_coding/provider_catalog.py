"""Built-in provider catalog for Tau login/setup flows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from tau_coding.thinking import ThinkingLevel, ThinkingParameter

ProviderKind = Literal["openai-compatible", "anthropic", "openai-codex"]


@dataclass(frozen=True, slots=True)
class ThinkingMode:
    """A canonical Tau thinking level's provider-specific behavior."""

    api_value: str | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderModelOverride:
    """Built-in model behavior that differs from its provider defaults."""

    kind: ProviderKind | None = None
    thinking_modes: Mapping[ThinkingLevel, ThinkingMode] | None = None
    thinking_default: ThinkingLevel | None = None
    always_thinking: bool = False


@dataclass(frozen=True, slots=True)
class ProviderCatalogEntry:
    """A built-in provider Tau can present during login."""

    name: str
    display_name: str
    kind: ProviderKind
    base_url: str
    api_key_env: str
    credential_name: str
    models: tuple[str, ...]
    default_model: str
    docs_url: str
    context_windows: dict[str, int] | None = None
    thinking_levels: tuple[ThinkingLevel, ...] | None = None
    thinking_models: tuple[str, ...] = ()
    thinking_default: ThinkingLevel | None = None
    thinking_parameter: ThinkingParameter | None = None
    model_overrides: dict[str, ProviderModelOverride] | None = None
    dynamic_models: bool = False


BUILTIN_PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = (
    ProviderCatalogEntry(
        name="openai",
        display_name="OpenAI",
        kind="openai-compatible",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        credential_name="openai",
        models=(
            "gpt-5.5",
            "gpt-5.5-pro",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.3-codex",
            "gpt-5.2",
            "gpt-5.1",
            "gpt-5",
            "gpt-5-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
        ),
        default_model="gpt-5.5",
        docs_url="https://platform.openai.com/docs",
        context_windows={
            "gpt-5.5": 272_000,
            "gpt-5.5-pro": 1_050_000,
            "gpt-5.4": 272_000,
            "gpt-5.4-mini": 400_000,
            "gpt-5.3-codex": 400_000,
            "gpt-5.2": 400_000,
            "gpt-5.1": 400_000,
            "gpt-5": 400_000,
            "gpt-5-mini": 400_000,
            "gpt-4.1": 1_047_576,
            "gpt-4.1-mini": 1_047_576,
        },
        thinking_levels=("off", "low", "medium", "high", "xhigh"),
        thinking_models=(
            "gpt-5.5",
            "gpt-5.5-pro",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.3-codex",
            "gpt-5.2",
            "gpt-5.1",
            "gpt-5",
            "gpt-5-mini",
        ),
        thinking_default="medium",
        thinking_parameter="reasoning_effort",
    ),
    ProviderCatalogEntry(
        name="openai-codex",
        display_name="OpenAI Codex subscription",
        kind="openai-codex",
        base_url="https://chatgpt.com/backend-api",
        api_key_env="OPENAI_CODEX_ACCESS_TOKEN",
        credential_name="openai-codex",
        models=(
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.3-codex",
            "gpt-5.3-codex-spark",
            "gpt-5.2",
        ),
        default_model="gpt-5.5",
        docs_url="https://chatgpt.com/codex",
        context_windows={
            "gpt-5.5": 272_000,
            "gpt-5.4": 272_000,
            "gpt-5.4-mini": 400_000,
            "gpt-5.3-codex": 400_000,
            "gpt-5.3-codex-spark": 400_000,
            "gpt-5.2": 400_000,
        },
        thinking_levels=("off", "minimal", "low", "medium", "high", "xhigh"),
        thinking_models=(
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.3-codex",
            "gpt-5.3-codex-spark",
            "gpt-5.2",
        ),
        thinking_default="medium",
        thinking_parameter="reasoning.effort",
    ),
    ProviderCatalogEntry(
        name="anthropic",
        display_name="Anthropic",
        kind="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
        credential_name="anthropic",
        models=(
            "claude-sonnet-4-6",
            "claude-opus-4-8",
            "claude-haiku-4-5",
        ),
        default_model="claude-sonnet-4-6",
        docs_url="https://docs.anthropic.com",
        context_windows={
            "claude-sonnet-4-6": 1_000_000,
            "claude-opus-4-8": 200_000,
            "claude-haiku-4-5": 200_000,
        },
        thinking_levels=("off", "minimal", "low", "medium", "high", "xhigh"),
        thinking_models=(
            "claude-sonnet-4-6",
            "claude-opus-4-8",
        ),
        thinking_default="medium",
        thinking_parameter="anthropic.thinking",
    ),
    ProviderCatalogEntry(
        name="github-copilot",
        display_name="GitHub Copilot",
        kind="openai-compatible",
        base_url="https://api.individual.githubcopilot.com",
        api_key_env="GITHUB_COPILOT_TOKEN",
        credential_name="github-copilot",
        models=(
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.3-codex",
            "gpt-5-mini",
            "claude-sonnet-5",
            "claude-sonnet-4.6",
            "claude-sonnet-4.5",
            "claude-opus-4.8",
            "claude-opus-4.7",
            "claude-opus-4.6",
            "claude-haiku-4.5",
            "gemini-3.5-flash",
            "gemini-3.1-pro-preview",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
        ),
        default_model="gpt-5.5",
        docs_url="https://docs.github.com/copilot",
        context_windows={
            "gpt-5.5": 272_000,
            "gpt-5.4": 272_000,
            "gpt-5.4-mini": 400_000,
            "gpt-5.3-codex": 400_000,
            "gpt-5-mini": 400_000,
            "claude-sonnet-5": 1_000_000,
            "claude-sonnet-4.6": 1_000_000,
            "claude-sonnet-4.5": 1_000_000,
            "claude-opus-4.8": 1_000_000,
            "claude-opus-4.7": 1_000_000,
            "claude-opus-4.6": 1_000_000,
            "claude-haiku-4.5": 200_000,
            "gemini-3.5-flash": 1_048_576,
            "gemini-3.1-pro-preview": 1_048_576,
            "gemini-3-flash-preview": 1_048_576,
            "gemini-2.5-pro": 1_048_576,
        },
        thinking_levels=("off", "low", "medium", "high", "xhigh"),
        thinking_models=(
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.3-codex",
            "gpt-5-mini",
        ),
        thinking_default="medium",
        thinking_parameter="reasoning_effort",
        dynamic_models=True,
    ),
    ProviderCatalogEntry(
        name="openrouter",
        display_name="OpenRouter",
        kind="openai-compatible",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        credential_name="openrouter",
        models=(
            "openai/gpt-5.5",
            "openai/gpt-5.4",
            "openai/gpt-5.3-codex",
            "anthropic/claude-sonnet-4.6",
            "anthropic/claude-opus-4.8",
            "google/gemini-3.5-pro",
            "moonshotai/kimi-k2.7-code",
            "moonshotai/kimi-k2-instruct",
            "deepseek/deepseek-v4-pro",
            "deepseek/deepseek-v4-flash",
            "z-ai/glm-5.2",
            "z-ai/glm-4.5",
            "minimax/minimax-m3",
            "qwen/qwen3-coder-plus",
            "qwen/qwen3-coder",
            "qwen/qwen3-235b-a22b-thinking-2507",
            "mistralai/codestral-2508",
            "meta-llama/llama-4-maverick",
        ),
        default_model="openai/gpt-5.5",
        docs_url="https://openrouter.ai/docs",
        context_windows={
            "openai/gpt-5.5": 1_050_000,
            "openai/gpt-5.4": 1_050_000,
            "openai/gpt-5.3-codex": 400_000,
            "anthropic/claude-sonnet-4.6": 1_000_000,
            "qwen/qwen3-coder-plus": 1_000_000,
            "mistralai/codestral-2508": 256_000,
        },
        thinking_levels=("off", "low", "medium", "high", "xhigh"),
        thinking_models=(
            "openai/gpt-5.5",
            "openai/gpt-5.4",
            "openai/gpt-5.3-codex",
            "qwen/qwen3-235b-a22b-thinking-2507",
        ),
        thinking_default="medium",
        thinking_parameter="reasoning_effort",
    ),
    ProviderCatalogEntry(
        name="huggingface",
        display_name="Hugging Face Inference Providers",
        kind="openai-compatible",
        base_url="https://router.huggingface.co/v1",
        api_key_env="HF_TOKEN",
        credential_name="huggingface",
        models=(
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "Qwen/Qwen3-Coder-480B-A35B-Instruct",
            "Qwen/Qwen3-Coder-Next",
            "Qwen/Qwen3-235B-A22B-Thinking-2507",
            "Qwen/Qwen2.5-Coder-32B-Instruct",
            "moonshotai/Kimi-K2.7-Code",
            "deepseek-ai/DeepSeek-V4-Pro",
            "deepseek-ai/DeepSeek-V4-Flash",
            "deepseek-ai/DeepSeek-R1",
            "moonshotai/Kimi-K2-Instruct",
            "zai-org/GLM-5.2",
            "zai-org/GLM-4.5",
            "MiniMaxAI/MiniMax-M3",
            "meta-llama/Llama-4-Maverick-17B-128E-Instruct",
            "mistralai/Codestral-22B-v0.1",
            "bigcode/starcoder2-15b",
        ),
        default_model="openai/gpt-oss-120b",
        docs_url="https://huggingface.co/inference/get-started",
        context_windows={
            "openai/gpt-oss-120b": 131_072,
            "openai/gpt-oss-20b": 131_072,
            "Qwen/Qwen3-Coder-480B-A35B-Instruct": 262_144,
        },
        thinking_levels=("low", "medium", "high"),
        thinking_models=(
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "Qwen/Qwen3-235B-A22B-Thinking-2507",
            "deepseek-ai/DeepSeek-R1",
        ),
        thinking_default="medium",
        thinking_parameter="reasoning_effort",
    ),
    ProviderCatalogEntry(
        name="deepseek",
        display_name="DeepSeek",
        kind="openai-compatible",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        credential_name="deepseek",
        models=(
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        ),
        default_model="deepseek-v4-flash",
        docs_url="https://api-docs.deepseek.com",
        context_windows={
            "deepseek-v4-flash": 1_048_576,
            "deepseek-v4-pro": 1_048_576,
        },
        thinking_levels=("off", "low", "medium", "high", "xhigh"),
        thinking_models=(
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        ),
        thinking_default="high",
        thinking_parameter="reasoning_effort",
    ),
    ProviderCatalogEntry(
        name="opencode-go",
        display_name="OpenCode Go",
        kind="openai-compatible",
        base_url="https://opencode.ai/zen/go/v1",
        api_key_env="OPENCODE_GO_API_KEY",
        credential_name="opencode-go",
        models=(
            "glm-5.2",
            "glm-5.1",
            "kimi-k2.7-code",
            "kimi-k2.6",
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "mimo-v2.5",
            "mimo-v2.5-pro",
            "minimax-m3",
            "minimax-m2.7",
            "qwen3.7-max",
            "qwen3.7-plus",
            "qwen3.6-plus",
        ),
        default_model="deepseek-v4-pro",
        docs_url="https://opencode.ai/docs/go",
        context_windows={
            "glm-5.2": 1_000_000,
            "glm-5.1": 202_752,
            "kimi-k2.7-code": 262_144,
            "kimi-k2.6": 262_144,
            "deepseek-v4-pro": 1_000_000,
            "deepseek-v4-flash": 1_000_000,
            "mimo-v2.5": 1_000_000,
            "mimo-v2.5-pro": 1_048_576,
            "minimax-m3": 1_000_000,
            "minimax-m2.7": 204_800,
            "qwen3.7-max": 1_000_000,
            "qwen3.7-plus": 1_000_000,
            "qwen3.6-plus": 1_000_000,
        },
        thinking_levels=("off", "low", "medium", "high", "xhigh"),
        thinking_models=(
            "glm-5.2",
            "deepseek-v4-pro",
            "deepseek-v4-flash",
            "minimax-m3",
            "qwen3.7-max",
            "qwen3.7-plus",
            "qwen3.6-plus",
        ),
        thinking_default="medium",
        thinking_parameter="reasoning_effort",
        model_overrides={
            "glm-5.2": ProviderModelOverride(
                thinking_modes={
                    "high": ThinkingMode(api_value="high"),
                    "xhigh": ThinkingMode(api_value="max", label="max"),
                },
                thinking_default="high",
            ),
            "glm-5.1": ProviderModelOverride(always_thinking=True),
            "kimi-k2.7-code": ProviderModelOverride(always_thinking=True),
            "kimi-k2.6": ProviderModelOverride(always_thinking=True),
            "deepseek-v4-pro": ProviderModelOverride(
                thinking_modes={
                    "high": ThinkingMode(api_value="high"),
                    "xhigh": ThinkingMode(api_value="max", label="max"),
                },
                thinking_default="high",
            ),
            "deepseek-v4-flash": ProviderModelOverride(
                thinking_modes={
                    "high": ThinkingMode(api_value="high"),
                    "xhigh": ThinkingMode(api_value="max", label="max"),
                },
                thinking_default="high",
            ),
            "mimo-v2.5": ProviderModelOverride(always_thinking=True),
            "mimo-v2.5-pro": ProviderModelOverride(always_thinking=True),
            "minimax-m3": ProviderModelOverride(
                kind="anthropic",
                thinking_modes={
                    "off": ThinkingMode(api_value="disabled"),
                    "high": ThinkingMode(api_value="adaptive", label="on"),
                },
                thinking_default="high",
            ),
            "minimax-m2.7": ProviderModelOverride(kind="anthropic", always_thinking=True),
            "qwen3.7-max": ProviderModelOverride(
                kind="anthropic",
                thinking_modes={
                    "off": ThinkingMode(api_value="disabled"),
                    "low": ThinkingMode(),
                    "medium": ThinkingMode(),
                    "high": ThinkingMode(),
                    "xhigh": ThinkingMode(),
                },
                thinking_default="medium",
            ),
            "qwen3.7-plus": ProviderModelOverride(
                kind="anthropic",
                thinking_modes={
                    "off": ThinkingMode(api_value="disabled"),
                    "low": ThinkingMode(),
                    "medium": ThinkingMode(),
                    "high": ThinkingMode(),
                    "xhigh": ThinkingMode(),
                },
                thinking_default="medium",
            ),
            "qwen3.6-plus": ProviderModelOverride(
                kind="anthropic",
                thinking_modes={
                    "off": ThinkingMode(api_value="disabled"),
                    "low": ThinkingMode(),
                    "medium": ThinkingMode(),
                    "high": ThinkingMode(),
                    "xhigh": ThinkingMode(),
                },
                thinking_default="medium",
            ),
        },
    ),
    ProviderCatalogEntry(
        name="nebius",
        display_name="Nebius Token Factory",
        kind="openai-compatible",
        base_url="https://api.tokenfactory.nebius.com/v1",
        api_key_env="NEBIUS_TOKEN_FACTORY_API_KEY",
        credential_name="nebius",
        models=(),
        default_model="",
        docs_url="https://docs.tokenfactory.nebius.com",
        dynamic_models=True,
    ),
)


def builtin_provider_entry(name: str) -> ProviderCatalogEntry | None:
    """Return a built-in catalog entry by provider name."""
    for entry in BUILTIN_PROVIDER_CATALOG:
        if entry.name == name:
            return entry
    return None


def catalog_model_override(provider_name: str, model: str | None) -> ProviderModelOverride | None:
    """Return built-in metadata for a provider/model pair."""
    if model is None:
        return None
    entry = builtin_provider_entry(provider_name)
    if entry is None or entry.model_overrides is None:
        return None
    return entry.model_overrides.get(model)
