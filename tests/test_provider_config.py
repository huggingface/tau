import json
from pathlib import Path

import pytest

from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.paths import TauPaths
from tau_coding.provider_catalog import ModelCostTier
from tau_coding.provider_config import (
    DEFAULT_MODEL,
    AnthropicProviderConfig,
    OpenAICodexProviderConfig,
    OpenAICompatibleProviderConfig,
    ProviderConfigError,
    ProviderModelMetadata,
    ProviderSettings,
    ScopedModelConfig,
    anthropic_config_from_provider,
    load_provider_settings,
    openai_compatible_config_from_provider,
    provider_default_thinking_level,
    provider_has_usable_credentials,
    provider_settings_from_json,
    provider_thinking_levels,
    provider_thinking_unavailable_reason,
    resolve_provider_selection,
    save_provider_settings,
    set_default_provider_model,
    set_provider_thinking_level,
    upsert_openai_compatible_provider,
)


def test_load_provider_settings_missing_file_uses_openai_default(tmp_path: Path) -> None:
    settings = load_provider_settings(TauPaths(home=tmp_path / ".tau"))

    assert settings.default_provider == "openai"
    assert [provider.name for provider in settings.providers] == [
        "openai",
        "openai-codex",
        "anthropic",
        "google",
        "deepseek",
        "xai",
        "groq",
        "cerebras",
        "nvidia",
        "openrouter",
        "zai",
        "mistral",
        "minimax",
        "minimax-cn",
        "moonshotai",
        "kimi-code",
        "moonshotai-cn",
        "huggingface",
        "fireworks",
        "together",
        "vercel-ai-gateway",
        "xiaomi",
        "xiaomi-token-plan-cn",
        "xiaomi-token-plan-ams",
        "xiaomi-token-plan-sgp",
    ]
    assert settings.providers[0].default_model == DEFAULT_MODEL
    assert settings.get_provider("anthropic").api_key_env == "ANTHROPIC_API_KEY"
    assert settings.get_provider("openrouter").api_key_env == "OPENROUTER_API_KEY"
    assert settings.get_provider("huggingface").api_key_env == "HF_TOKEN"


def test_builtin_openai_declares_model_scoped_thinking_capabilities() -> None:
    settings = ProviderSettings()
    openai = settings.get_provider("openai")
    openrouter = settings.get_provider("openrouter")
    huggingface = settings.get_provider("huggingface")
    codex = settings.get_provider("openai-codex")
    anthropic = settings.get_provider("anthropic")

    assert openai.context_windows["gpt-5.5"] == 272_000
    assert openai.context_windows["gpt-5.5-pro"] == 1_050_000
    assert settings.get_provider("anthropic").context_windows["claude-sonnet-4-6"] == 1_000_000
    assert openrouter.context_windows["openai/gpt-5.5"] == 1_050_000
    assert provider_thinking_levels(openai, model="gpt-5.5") == (
        "off",
        "low",
        "medium",
        "high",
        "xhigh",
    )
    assert provider_default_thinking_level(openai, model="gpt-5.5") == "medium"
    assert provider_thinking_unavailable_reason(openai, model="gpt-5.5") is None
    assert provider_thinking_levels(openai, model="gpt-4.1") == ()
    assert (
        provider_thinking_unavailable_reason(openai, model="gpt-4.1")
        == "openai:gpt-4.1 is not a reasoning model"
    )
    assert provider_thinking_levels(openrouter, model="openai/gpt-5.5") == (
        "off",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    )
    assert provider_thinking_unavailable_reason(openrouter, model="openai/gpt-5.5") is None
    assert provider_thinking_levels(openrouter, model="anthropic/claude-sonnet-4.6") == (
        "off",
        "minimal",
        "low",
        "medium",
        "high",
    )
    assert (
        provider_thinking_unavailable_reason(openrouter, model="anthropic/claude-sonnet-4.6")
        is None
    )
    assert provider_thinking_levels(huggingface, model="MiniMaxAI/MiniMax-M2.7") == (
        "off",
        "minimal",
        "low",
        "medium",
        "high",
    )
    assert provider_thinking_unavailable_reason(huggingface, model="MiniMaxAI/MiniMax-M2.7") is None
    assert provider_thinking_levels(codex, model="gpt-5.5") == (
        "off",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    )
    assert provider_thinking_unavailable_reason(codex, model="gpt-5.5") is None
    assert provider_thinking_levels(anthropic, model="claude-sonnet-4-6") == (
        "off",
        "low",
        "medium",
        "high",
    )
    assert provider_thinking_unavailable_reason(anthropic, model="claude-sonnet-4-6") is None
    assert provider_thinking_levels(anthropic, model="claude-haiku-4-5") == (
        "off",
        "minimal",
        "low",
        "medium",
        "high",
    )


def test_load_provider_settings_accepts_provider_preferences_with_user_catalog(
    tmp_path: Path,
) -> None:
    tau_home = tmp_path / ".tau"
    tau_home.mkdir()
    (tau_home / "catalog.toml").write_text(
        """
schema_version = 1

[[providers]]
name = "local"
display_name = "local"
kind = "openai-compatible"
base_url = "http://localhost:11434/v1"
api_key_env = "LOCAL_API_KEY"
models = ["qwen", "llama"]
default_model = "qwen"
docs_url = "http://localhost:11434/v1"
""".strip(),
        encoding="utf-8",
    )
    (tau_home / "providers.json").write_text(
        json.dumps(
            {
                "default_provider": "local",
                "provider_preferences": {
                    "local": {
                        "default_model": "qwen",
                        "headers": {"X-Test": "yes"},
                        "timeout_seconds": 12.0,
                        "max_retries": 1,
                        "max_retry_delay_seconds": 0.5,
                        "thinking_defaults": {},
                    }
                },
                "scoped_models": [{"provider": "local", "model": "qwen"}],
            }
        ),
        encoding="utf-8",
    )

    settings = load_provider_settings(TauPaths(home=tau_home))

    provider = settings.get_provider("local")
    assert settings.default_provider == "local"
    assert provider.base_url == "http://localhost:11434/v1"
    assert provider.default_model == "qwen"
    assert provider.headers == {"X-Test": "yes"}
    assert provider.timeout_seconds == 12.0
    assert settings.scoped_models == (ScopedModelConfig(provider="local", model="qwen"),)


def test_load_provider_settings_ignores_preference_without_catalog_entry(
    tmp_path: Path,
) -> None:
    tau_home = tmp_path / ".tau"
    tau_home.mkdir()
    (tau_home / "providers.json").write_text(
        json.dumps(
            {
                "default_provider": "openai",
                "provider_preferences": {
                    "openai": {"default_model": "gpt-5-mini"},
                    "llama-cpp": {"default_model": "local"},
                },
            }
        ),
        encoding="utf-8",
    )

    settings = load_provider_settings(TauPaths(home=tau_home))

    assert settings.get_provider("openai").default_model == "gpt-5-mini"
    assert "llama-cpp" not in {provider.name for provider in settings.providers}


def test_save_provider_settings_writes_backup_when_replacing(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau")
    initial = ProviderSettings(
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5",),
                default_model="gpt-5",
            ),
        ),
    )
    updated = ProviderSettings(
        providers=(
            OpenAICompatibleProviderConfig(
                name="openai",
                models=("gpt-5-mini",),
                default_model="gpt-5-mini",
            ),
        ),
    )

    path = save_provider_settings(initial, paths)
    save_provider_settings(updated, paths)

    backup = path.with_suffix(path.suffix + ".bak")
    assert backup.exists()
    assert load_provider_settings(paths).get_provider("openai").default_model == "gpt-5-mini"
    assert (
        provider_settings_from_json(json.loads(backup.read_text()))
        .get_provider("openai")
        .default_model
        == "gpt-5"
    )


def test_save_and_load_provider_settings_round_trip(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau")
    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen", "llama"),
                default_model="qwen",
                context_windows={"qwen": 64_000},
                headers={"X-Test": "enabled"},
                timeout_seconds=120,
                max_retries=2,
                max_retry_delay_seconds=0.5,
            ),
        ),
        scoped_models=(ScopedModelConfig(provider="local", model="llama"),),
    )

    path = save_provider_settings(settings, paths)
    loaded = load_provider_settings(paths)

    assert path == tmp_path / ".tau" / "providers.json"
    assert loaded == settings


def test_legacy_provider_model_cost_tiers_round_trip() -> None:
    raw = {
        "default_provider": "local",
        "providers": [
            {
                "type": "openai-compatible",
                "name": "local",
                "base_url": "http://localhost:11434/v1",
                "api_key_env": "LOCAL_API_KEY",
                "models": ["qwen"],
                "default_model": "qwen",
                "model_metadata": {
                    "qwen": {
                        "cost": {
                            "input": 0.3,
                            "output": 1.2,
                            "cacheRead": 0.06,
                            "cacheWrite": 0,
                        },
                        "cost_tiers": [
                            {
                                "max_input_tokens": 512000,
                                "input": 0.3,
                                "output": 1.2,
                                "cacheRead": 0.06,
                                "cacheWrite": 0,
                            },
                            {
                                "input": 0.6,
                                "output": 2.4,
                                "cacheRead": 0.12,
                                "cacheWrite": 0,
                            },
                        ],
                    }
                },
            }
        ],
        "scoped_models": [],
    }

    settings = provider_settings_from_json(raw)
    provider = settings.get_provider("local")
    assert isinstance(provider, OpenAICompatibleProviderConfig)
    assert (
        provider.model_metadata["qwen"].to_json()["cost_tiers"]
        == raw["providers"][0]["model_metadata"]["qwen"]["cost_tiers"]
    )


@pytest.mark.parametrize(
    ("cost_tiers", "match"),
    [
        (
            [
                {
                    "max_input_tokens": 512000,
                    "input": 0.3,
                    "output": 1.2,
                    "cacheRead": 0.06,
                    "cacheWrite": 0,
                }
            ],
            "final cost tier must omit max_input_tokens",
        ),
        (
            [
                {
                    "max_input_tokens": 512000,
                    "input": 0.3,
                    "output": 1.2,
                    "cacheRead": 0.06,
                    "cacheWrite": 0,
                },
                {
                    "max_input_tokens": 400000,
                    "input": 0.4,
                    "output": 1.6,
                    "cacheRead": 0.08,
                    "cacheWrite": 0,
                },
                {
                    "input": 0.6,
                    "output": 2.4,
                    "cacheRead": 0.12,
                    "cacheWrite": 0,
                },
            ],
            "limits must be strictly increasing",
        ),
        (
            [
                {
                    "unexpected": 1,
                    "input": 0.3,
                    "output": 1.2,
                    "cacheRead": 0.06,
                    "cacheWrite": 0,
                }
            ],
            "unknown fields",
        ),
        (
            [
                {
                    "input": -0.3,
                    "output": 1.2,
                    "cacheRead": 0.06,
                    "cacheWrite": 0,
                }
            ],
            "0 or greater",
        ),
    ],
)
def test_legacy_provider_rejects_invalid_cost_tiers(
    cost_tiers: list[dict[str, object]],
    match: str,
) -> None:
    raw = {
        "default_provider": "local",
        "providers": [
            {
                "type": "openai-compatible",
                "name": "local",
                "base_url": "http://localhost:11434/v1",
                "api_key_env": "LOCAL_API_KEY",
                "models": ["qwen"],
                "default_model": "qwen",
                "model_metadata": {"qwen": {"cost_tiers": cost_tiers}},
            }
        ],
    }

    with pytest.raises(ProviderConfigError, match=match):
        provider_settings_from_json(raw)


def test_runtime_metadata_rejects_invalid_cost_tier_values() -> None:
    with pytest.raises(ProviderConfigError, match="cost tier values must be non-negative"):
        OpenAICompatibleProviderConfig(
            name="local",
            models=("qwen",),
            default_model="qwen",
            model_metadata={
                "qwen": ProviderModelMetadata(
                    cost_tiers=(
                        ModelCostTier(
                            cost={
                                "input": -0.3,
                                "output": 1.2,
                                "cacheRead": 0.06,
                                "cacheWrite": 0,
                            }
                        ),
                    )
                )
            },
        )


def test_provider_settings_parses_scoped_models() -> None:
    settings = provider_settings_from_json(
        {
            "default_provider": "local",
            "providers": [
                {
                    "type": "openai-compatible",
                    "name": "local",
                    "base_url": "http://localhost:11434/v1",
                    "api_key_env": "LOCAL_API_KEY",
                    "models": ["qwen", "llama"],
                    "default_model": "qwen",
                    "context_windows": {"qwen": 64000},
                }
            ],
            "scoped_models": [
                {"provider": "local", "model": "qwen"},
                {"provider": "local", "model": "qwen"},
                {"provider": "local", "model": "llama"},
            ],
        }
    )

    assert settings.get_provider("local").context_windows == {"qwen": 64000}
    assert settings.scoped_models == (
        ScopedModelConfig(provider="local", model="qwen"),
        ScopedModelConfig(provider="local", model="llama"),
    )


def test_upsert_openai_compatible_provider_replaces_and_sets_default() -> None:
    settings = ProviderSettings(
        scoped_models=(ScopedModelConfig(provider="openai", model="gpt-5.5"),)
    )
    provider = OpenAICompatibleProviderConfig(
        name="local",
        base_url="http://localhost:11434/v1",
        api_key_env="LOCAL_API_KEY",
        models=("qwen",),
        default_model="qwen",
    )

    updated = upsert_openai_compatible_provider(settings, provider, set_default=True)
    replaced = upsert_openai_compatible_provider(
        updated,
        OpenAICompatibleProviderConfig(
            name="local",
            base_url="http://localhost:11434/v1",
            api_key_env="LOCAL_API_KEY",
            models=("llama",),
            default_model="llama",
        ),
        set_default=True,
    )

    assert updated.default_provider == "local"
    assert [item.name for item in updated.providers] == sorted(
        [provider.name for provider in settings.providers] + ["local"]
    )
    assert replaced.get_provider("local").default_model == "llama"
    assert replaced.scoped_models == settings.scoped_models


def test_resolve_provider_selection_uses_configured_defaults() -> None:
    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen",),
                default_model="qwen",
            ),
        ),
    )

    selection = resolve_provider_selection(settings)

    assert selection.provider.name == "local"
    assert selection.model == "qwen"


def test_resolve_provider_selection_rejects_unknown_provider() -> None:
    with pytest.raises(ProviderConfigError, match="Unknown provider"):
        resolve_provider_selection(ProviderSettings(), provider_name="missing")


def test_resolve_provider_selection_rejects_model_not_declared_for_provider() -> None:
    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen",),
                default_model="qwen",
            ),
        ),
    )

    with pytest.raises(
        ProviderConfigError,
        match="Model is not configured for provider local: llama",
    ):
        resolve_provider_selection(settings, model="llama")


def test_set_default_provider_model_rejects_model_not_declared_for_provider() -> None:
    settings = ProviderSettings(
        default_provider="local",
        providers=(
            OpenAICompatibleProviderConfig(
                name="local",
                base_url="http://localhost:11434/v1",
                api_key_env="LOCAL_API_KEY",
                models=("qwen",),
                default_model="qwen",
            ),
        ),
    )

    with pytest.raises(
        ProviderConfigError,
        match="Model is not configured for provider local: llama",
    ):
        set_default_provider_model(settings, provider_name="local", model="llama")


def test_openai_compatible_config_from_provider_uses_configured_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")
    provider = OpenAICompatibleProviderConfig(
        name="local",
        base_url="http://localhost:11434/v1/",
        api_key_env="LOCAL_API_KEY",
        models=("qwen",),
        default_model="qwen",
    )

    config = openai_compatible_config_from_provider(provider)

    assert config.api_key == "test-key"
    assert config.provider_name == "local"
    assert config.base_url == "http://localhost:11434/v1"
    assert config.headers == {}
    assert config.timeout_seconds == 60.0
    assert config.max_retries == 2
    assert config.max_retry_delay_seconds == 1.0


def test_openai_compatible_config_from_provider_preserves_openai_base_url_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1/")

    class FakeCredentials:
        def get(self, name: str) -> str | None:
            return "stored-key" if name == "openai" else None

    config = openai_compatible_config_from_provider(
        OpenAICompatibleProviderConfig(name="openai", credential_name="openai"),
        credential_reader=FakeCredentials(),
    )

    assert config.api_key == "stored-key"
    assert config.base_url == "https://proxy.example/v1"


def test_openai_compatible_config_from_provider_uses_configured_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")
    provider = OpenAICompatibleProviderConfig(
        name="local",
        base_url="http://localhost:11434/v1/",
        api_key_env="LOCAL_API_KEY",
        models=("qwen",),
        default_model="qwen",
        timeout_seconds=180,
        max_retries=3,
        max_retry_delay_seconds=0.25,
    )

    config = openai_compatible_config_from_provider(provider)

    assert config.timeout_seconds == 180
    assert config.max_retries == 3
    assert config.max_retry_delay_seconds == 0.25


def test_openai_compatible_config_from_provider_uses_configured_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")
    provider = OpenAICompatibleProviderConfig(
        name="local",
        base_url="http://localhost:11434/v1/",
        api_key_env="LOCAL_API_KEY",
        models=("qwen",),
        default_model="qwen",
        headers={"X-HF-Bill-To": "my-org"},
    )

    config = openai_compatible_config_from_provider(provider)

    assert config.headers == {"X-HF-Bill-To": "my-org"}


def test_openai_compatible_config_from_provider_sets_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")
    provider = OpenAICompatibleProviderConfig(
        name="local",
        base_url="http://localhost:11434/v1/",
        api_key_env="LOCAL_API_KEY",
        models=("reasoner", "plain"),
        default_model="reasoner",
        thinking_levels=("off", "low", "high"),
        thinking_models=("reasoner",),
        thinking_default="low",
        thinking_parameter="reasoning_effort",
    )

    reasoner = openai_compatible_config_from_provider(
        provider,
        model="reasoner",
        thinking_level="off",
    )
    plain = openai_compatible_config_from_provider(
        provider,
        model="plain",
        thinking_level="high",
    )

    assert reasoner.reasoning_effort == "none"
    assert plain.reasoning_effort is None


def test_openai_compatible_config_from_provider_rejects_unsupported_thinking_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")
    provider = OpenAICompatibleProviderConfig(
        name="local",
        base_url="http://localhost:11434/v1/",
        api_key_env="LOCAL_API_KEY",
        models=("reasoner",),
        default_model="reasoner",
        thinking_levels=("low", "high"),
        thinking_parameter="reasoning_effort",
    )

    with pytest.raises(ProviderConfigError, match="not available"):
        openai_compatible_config_from_provider(
            provider,
            model="reasoner",
            thinking_level="medium",
        )


def test_openai_compatible_config_from_provider_uses_stored_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    provider = OpenAICompatibleProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        credential_name="openrouter",
        models=("openai/gpt-4.1-mini",),
        default_model="openai/gpt-4.1-mini",
    )

    class FakeCredentials:
        def get(self, name: str) -> str | None:
            return "stored-key" if name == "openrouter" else None

    config = openai_compatible_config_from_provider(
        provider,
        credential_reader=FakeCredentials(),
    )

    assert config.api_key == "stored-key"


def test_openai_compatible_config_from_provider_falls_back_to_env_when_stored_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    provider = OpenAICompatibleProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        credential_name="openrouter",
        models=("openai/gpt-4.1-mini",),
        default_model="openai/gpt-4.1-mini",
    )

    class FakeCredentials:
        def get(self, name: str) -> str | None:
            return None

    config = openai_compatible_config_from_provider(provider, credential_reader=FakeCredentials())

    assert config.api_key == "env-key"


def test_provider_has_usable_credentials_checks_stored_key_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    provider = OpenAICompatibleProviderConfig(
        name="openrouter",
        api_key_env="OPENROUTER_API_KEY",
        credential_name="openrouter",
    )

    class EmptyCredentials:
        def get(self, name: str) -> str | None:
            return None

    class StoredCredentials:
        def get(self, name: str) -> str | None:
            return "stored-key" if name == "openrouter" else None

    assert not provider_has_usable_credentials(provider, credential_reader=EmptyCredentials())
    assert provider_has_usable_credentials(provider, credential_reader=StoredCredentials())

    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")

    assert provider_has_usable_credentials(provider, credential_reader=EmptyCredentials())


def test_anthropic_config_from_provider_uses_stored_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProviderConfig(credential_name="anthropic")

    class FakeCredentials:
        def get(self, name: str) -> str | None:
            return "stored-anthropic-key" if name == "anthropic" else None

    config = anthropic_config_from_provider(provider, credential_reader=FakeCredentials())

    assert config.api_key == "stored-anthropic-key"
    assert config.base_url == "https://api.anthropic.com/v1"


def test_anthropic_config_from_provider_sets_thinking_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    provider = AnthropicProviderConfig(
        thinking_levels=("off", "low", "high"),
        thinking_default="low",
        thinking_parameter="anthropic.thinking",
    )

    off_config = anthropic_config_from_provider(provider, thinking_level="off")
    high_config = anthropic_config_from_provider(provider, thinking_level="high")

    assert off_config.thinking_budget_tokens is None
    assert high_config.thinking_budget_tokens == 8192


@pytest.mark.parametrize(
    ("parameter", "expected"),
    [
        ("reasoning_effort", "reasoning_effort"),
        ("reasoning.effort", "reasoning.effort"),
    ],
)
def test_openai_compatible_config_from_provider_sets_reasoning_parameter(
    monkeypatch: pytest.MonkeyPatch,
    parameter: str,
    expected: str,
) -> None:
    monkeypatch.setenv("LOCAL_API_KEY", "test-key")
    provider = OpenAICompatibleProviderConfig(
        name="local",
        base_url="http://localhost:11434/v1/",
        api_key_env="LOCAL_API_KEY",
        models=("reasoner",),
        default_model="reasoner",
        thinking_levels=("low", "high"),
        thinking_parameter=parameter,  # type: ignore[arg-type]
    )

    config = openai_compatible_config_from_provider(
        provider,
        model="reasoner",
        thinking_level="high",
    )

    assert config.reasoning_effort == "high"
    assert config.reasoning_effort_parameter == expected


def test_provider_settings_from_json_loads_headers() -> None:
    settings = provider_settings_from_json(
        {
            "default_provider": "huggingface",
            "providers": [
                {
                    "type": "openai-compatible",
                    "name": "huggingface",
                    "base_url": "https://router.huggingface.co/v1",
                    "api_key_env": "HF_TOKEN",
                    "credential_name": "huggingface",
                    "models": ["Qwen/Qwen3-Coder"],
                    "default_model": "Qwen/Qwen3-Coder",
                    "headers": {"X-HF-Bill-To": "my-org"},
                }
            ],
        }
    )

    provider = settings.get_provider("huggingface")

    assert isinstance(provider, OpenAICompatibleProviderConfig)
    assert provider.headers == {"X-HF-Bill-To": "my-org"}


def test_provider_settings_from_json_loads_custom_thinking_capabilities() -> None:
    settings = provider_settings_from_json(
        {
            "default_provider": "local",
            "providers": [
                {
                    "type": "openai-compatible",
                    "name": "local",
                    "base_url": "http://localhost:11434/v1",
                    "api_key_env": "LOCAL_API_KEY",
                    "models": ["reasoner", "plain"],
                    "default_model": "reasoner",
                    "thinking_levels": ["off", "low", "high"],
                    "thinking_models": ["reasoner"],
                    "thinking_default": "low",
                    "thinking_parameter": "reasoning_effort",
                    "thinking_defaults": {"reasoner": "high"},
                }
            ],
        }
    )

    provider = settings.get_provider("local")

    assert isinstance(provider, OpenAICompatibleProviderConfig)
    assert provider_thinking_levels(provider, model="reasoner") == ("off", "low", "high")
    assert provider_thinking_levels(provider, model="plain") == ()
    assert provider_default_thinking_level(provider, model="reasoner") == "low"
    assert provider.thinking_defaults == {"reasoner": "high"}
    assert provider.to_json()["thinking_parameter"] == "reasoning_effort"


def test_set_provider_thinking_level_updates_preference() -> None:
    provider = OpenAICompatibleProviderConfig(
        name="local",
        models=("reasoner",),
        default_model="reasoner",
        thinking_levels=("low", "high"),
        thinking_models=("reasoner",),
        thinking_default="low",
        thinking_parameter="reasoning_effort",
    )
    settings = ProviderSettings(default_provider="local", providers=(provider,))

    updated = set_provider_thinking_level(
        settings,
        provider_name="local",
        model="reasoner",
        thinking_level="high",
    )

    assert updated.get_provider("local").thinking_defaults == {"reasoner": "high"}
    assert updated.to_json()["provider_preferences"]["local"]["thinking_defaults"] == {
        "reasoner": "high"
    }


def test_provider_settings_from_json_loads_openai_codex_provider() -> None:
    settings = provider_settings_from_json(
        {
            "default_provider": "openai-codex",
            "providers": [
                {
                    "type": "openai-codex",
                    "name": "openai-codex",
                    "base_url": "https://chatgpt.com/backend-api",
                    "api_key_env": "OPENAI_CODEX_ACCESS_TOKEN",
                    "credential_name": "openai-codex",
                    "models": ["gpt-5.5", "gpt-5.4"],
                    "default_model": "gpt-5.5",
                    "headers": {"X-Test": "enabled"},
                }
            ],
        }
    )

    provider = settings.get_provider("openai-codex")

    assert isinstance(provider, OpenAICodexProviderConfig)
    assert provider.default_model == "gpt-5.5"
    assert provider.headers == {"X-Test": "enabled"}


def test_provider_settings_from_json_loads_anthropic_thinking_provider() -> None:
    settings = provider_settings_from_json(
        {
            "default_provider": "anthropic",
            "providers": [
                {
                    "type": "anthropic",
                    "name": "anthropic",
                    "base_url": "https://api.anthropic.com/v1",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "models": ["claude-sonnet-4-6"],
                    "default_model": "claude-sonnet-4-6",
                    "thinking_levels": ["off", "low", "high"],
                    "thinking_models": ["claude-sonnet-4-6"],
                    "thinking_parameter": "anthropic.thinking",
                }
            ],
        }
    )

    provider = settings.get_provider("anthropic")

    assert isinstance(provider, AnthropicProviderConfig)
    assert provider_thinking_levels(provider, model="claude-sonnet-4-6") == (
        "off",
        "low",
        "high",
    )
    assert provider.thinking_parameter == "anthropic.thinking"


def test_load_provider_settings_does_not_restore_stale_codex_builtin_models(
    tmp_path: Path,
) -> None:
    tau_home = tmp_path / ".tau"
    tau_home.mkdir()
    (tau_home / "providers.json").write_text(
        """
{
  "default_provider": "openai-codex",
  "providers": [
    {
      "type": "openai-codex",
      "name": "openai-codex",
      "base_url": "https://chatgpt.com/backend-api",
      "api_key_env": "OPENAI_CODEX_ACCESS_TOKEN",
      "credential_name": "openai-codex",
      "models": ["gpt-5", "gpt-5.5"],
      "default_model": "gpt-5"
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    settings = load_provider_settings(TauPaths(home=tau_home))
    provider = settings.get_provider("openai-codex")

    assert provider.models == (
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
        "gpt-5.3-codex-spark",
        "gpt-5.2",
    )
    assert provider.default_model == "gpt-5.5"


def test_load_provider_settings_merges_builtin_model_catalog(tmp_path: Path) -> None:
    tau_home = tmp_path / ".tau"
    tau_home.mkdir()
    (tau_home / "providers.json").write_text(
        """
{
  "default_provider": "huggingface",
  "providers": [
    {
      "type": "openai-compatible",
      "name": "huggingface",
      "base_url": "https://router.huggingface.co/v1",
      "api_key_env": "HF_TOKEN",
      "credential_name": "huggingface",
      "models": ["MiniMaxAI/MiniMax-M2.7", "custom/coder"],
      "default_model": "MiniMaxAI/MiniMax-M2.7",
      "headers": {"X-HF-Bill-To": "my-org"}
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    settings = load_provider_settings(TauPaths(home=tau_home))

    provider = settings.get_provider("huggingface")
    assert provider.default_model == "MiniMaxAI/MiniMax-M2.7"
    assert provider.headers == {"X-HF-Bill-To": "my-org"}
    assert provider.context_windows["MiniMaxAI/MiniMax-M2.7"] == 204_800
    assert "Qwen/Qwen3-Coder-480B-A35B-Instruct" in provider.models
    assert "moonshotai/Kimi-K2.6" in provider.models
    assert "custom/coder" in provider.models


def test_load_provider_settings_restores_builtin_providers_with_stored_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for env_name in (
        "OPENAI_API_KEY",
        "OPENAI_CODEX_ACCESS_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "HF_TOKEN",
    ):
        monkeypatch.delenv(env_name, raising=False)
    tau_home = tmp_path / ".tau"
    tau_home.mkdir()
    (tau_home / "providers.json").write_text(
        """
{
  "default_provider": "local",
  "providers": [
    {
      "type": "openai-compatible",
      "name": "local",
      "base_url": "http://localhost:11434/v1",
      "api_key_env": "LOCAL_API_KEY",
      "credential_name": null,
      "models": ["qwen"],
      "default_model": "qwen"
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )
    store = FileCredentialStore(tau_home / "credentials.json")
    store.set("openrouter", "stored-openrouter-key")
    store.set_oauth(
        "openai-codex",
        OAuthCredential(
            access="access-token",
            refresh="refresh-token",
            expires=123456,
            account_id="account-1",
        ),
    )

    settings = load_provider_settings(TauPaths(home=tau_home))

    assert [provider.name for provider in settings.providers] == [
        "local",
        "openai-codex",
        "openrouter",
    ]
    assert settings.default_provider == "local"
    assert settings.get_provider("openrouter").credential_name == "openrouter"
    assert settings.get_provider("openai-codex").credential_name == "openai-codex"


def test_load_provider_settings_restores_builtin_credential_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    tau_home = tmp_path / ".tau"
    tau_home.mkdir()
    (tau_home / "providers.json").write_text(
        """
{
  "default_provider": "openrouter",
  "providers": [
    {
      "type": "openai-compatible",
      "name": "openrouter",
      "base_url": "https://openrouter.ai/api/v1",
      "api_key_env": "OPENROUTER_API_KEY",
      "credential_name": null,
      "models": ["openai/gpt-5.5"],
      "default_model": "openai/gpt-5.5"
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    class FakeCredentials:
        def get(self, name: str) -> str | None:
            return "stored-key" if name == "openrouter" else None

    settings = load_provider_settings(TauPaths(home=tau_home))
    provider = settings.get_provider("openrouter")

    assert isinstance(provider, OpenAICompatibleProviderConfig)
    assert provider.credential_name == "openrouter"
    assert provider.context_windows["openai/gpt-5.5"] == 1_050_000
    config = openai_compatible_config_from_provider(
        provider,
        credential_reader=FakeCredentials(),
    )
    assert config.api_key == "stored-key"


def test_provider_settings_from_json_rejects_invalid_headers() -> None:
    with pytest.raises(ProviderConfigError, match="string object"):
        provider_settings_from_json(
            {
                "default_provider": "local",
                "providers": [
                    {
                        "type": "openai-compatible",
                        "name": "local",
                        "base_url": "http://localhost:11434/v1",
                        "api_key_env": "LOCAL_API_KEY",
                        "models": ["qwen"],
                        "default_model": "qwen",
                        "headers": {"X-Test": 123},
                    }
                ],
            }
        )


def test_provider_settings_from_json_rejects_invalid_timeout() -> None:
    with pytest.raises(ProviderConfigError, match="greater than 0"):
        provider_settings_from_json(
            {
                "default_provider": "local",
                "providers": [
                    {
                        "type": "openai-compatible",
                        "name": "local",
                        "base_url": "http://localhost:11434/v1",
                        "api_key_env": "LOCAL_API_KEY",
                        "models": ["qwen"],
                        "default_model": "qwen",
                        "timeout_seconds": 0,
                    }
                ],
            }
        )


def test_openai_compatible_provider_config_rejects_invalid_timeout() -> None:
    with pytest.raises(ProviderConfigError, match="greater than 0"):
        OpenAICompatibleProviderConfig(name="local", timeout_seconds=0)


def test_provider_settings_from_json_rejects_invalid_retries() -> None:
    with pytest.raises(ProviderConfigError, match="0 or greater"):
        provider_settings_from_json(
            {
                "default_provider": "local",
                "providers": [
                    {
                        "type": "openai-compatible",
                        "name": "local",
                        "base_url": "http://localhost:11434/v1",
                        "api_key_env": "LOCAL_API_KEY",
                        "models": ["qwen"],
                        "default_model": "qwen",
                        "max_retries": -1,
                    }
                ],
            }
        )


def test_openai_compatible_provider_config_rejects_invalid_retries() -> None:
    with pytest.raises(ProviderConfigError, match="0 or greater"):
        OpenAICompatibleProviderConfig(name="local", max_retries=-1)
    with pytest.raises(ProviderConfigError, match="0 or greater"):
        OpenAICompatibleProviderConfig(name="local", max_retry_delay_seconds=-1)
