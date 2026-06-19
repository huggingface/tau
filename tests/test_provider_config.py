from pathlib import Path

import pytest

from tau_coding.paths import TauPaths
from tau_coding.provider_config import (
    DEFAULT_MODEL,
    AnthropicProviderConfig,
    OpenAICodexProviderConfig,
    OpenAICompatibleProviderConfig,
    ProviderConfigError,
    ProviderSettings,
    ScopedModelConfig,
    anthropic_config_from_provider,
    load_provider_settings,
    openai_compatible_config_from_provider,
    provider_default_thinking_level,
    provider_has_usable_credentials,
    provider_settings_from_json,
    provider_thinking_levels,
    resolve_provider_selection,
    save_provider_settings,
    upsert_openai_compatible_provider,
)


def test_load_provider_settings_missing_file_uses_openai_default(tmp_path: Path) -> None:
    settings = load_provider_settings(TauPaths(home=tmp_path / ".tau"))

    assert settings.default_provider == "openai"
    assert [provider.name for provider in settings.providers] == [
        "openai",
        "openai-codex",
        "anthropic",
        "openrouter",
        "huggingface",
    ]
    assert settings.providers[0].default_model == DEFAULT_MODEL
    assert settings.get_provider("anthropic").api_key_env == "ANTHROPIC_API_KEY"
    assert settings.get_provider("openrouter").api_key_env == "OPENROUTER_API_KEY"
    assert settings.get_provider("huggingface").api_key_env == "HF_TOKEN"


def test_builtin_openai_declares_model_scoped_thinking_capabilities() -> None:
    settings = ProviderSettings()
    openai = settings.get_provider("openai")
    openrouter = settings.get_provider("openrouter")

    assert provider_thinking_levels(openai, model="gpt-5.5") == (
        "off",
        "low",
        "medium",
        "high",
        "xhigh",
    )
    assert provider_default_thinking_level(openai, model="gpt-5.5") == "medium"
    assert provider_thinking_levels(openai, model="gpt-4.1") == ()
    assert provider_thinking_levels(openrouter, model="openai/gpt-5.5") == ()


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
                }
            ],
            "scoped_models": [
                {"provider": "local", "model": "qwen"},
                {"provider": "local", "model": "qwen"},
                {"provider": "local", "model": "llama"},
            ],
        }
    )

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
    assert [item.name for item in updated.providers] == [
        "anthropic",
        "huggingface",
        "local",
        "openai",
        "openai-codex",
        "openrouter",
    ]
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
                }
            ],
        }
    )

    provider = settings.get_provider("local")

    assert isinstance(provider, OpenAICompatibleProviderConfig)
    assert provider_thinking_levels(provider, model="reasoner") == ("off", "low", "high")
    assert provider_thinking_levels(provider, model="plain") == ()
    assert provider_default_thinking_level(provider, model="reasoner") == "low"
    assert provider.to_json()["thinking_parameter"] == "reasoning_effort"


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


def test_provider_settings_from_json_rejects_unimplemented_thinking_provider() -> None:
    with pytest.raises(ProviderConfigError, match="Anthropic thinking controls"):
        provider_settings_from_json(
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
                        "thinking_levels": ["low", "high"],
                    }
                ],
            }
        )


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
      "models": ["openai/gpt-oss-120b", "custom/coder"],
      "default_model": "openai/gpt-oss-120b",
      "headers": {"X-HF-Bill-To": "my-org"}
    }
  ]
}
""".strip(),
        encoding="utf-8",
    )

    settings = load_provider_settings(TauPaths(home=tau_home))

    provider = settings.get_provider("huggingface")
    assert provider.default_model == "openai/gpt-oss-120b"
    assert provider.headers == {"X-HF-Bill-To": "my-org"}
    assert "Qwen/Qwen3-Coder-480B-A35B-Instruct" in provider.models
    assert "MiniMaxAI/MiniMax-M3" in provider.models
    assert "moonshotai/Kimi-K2.7-Code" in provider.models
    assert "custom/coder" in provider.models


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
