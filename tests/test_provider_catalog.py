"""Tests for the TOML-backed provider catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from tau_coding.catalog_loader import (
    CatalogError,
    builtin_catalog,
    builtin_catalog_resource_text,
    effective_catalog,
    user_catalog_path,
)
from tau_coding.paths import TauPaths
from tau_coding.provider_catalog import BUILTIN_PROVIDER_CATALOG, builtin_provider_entry
from tau_coding.provider_config import load_provider_settings

VALID_PROVIDER = """
[[providers]]
name = "nebius"
display_name = "Nebius AI Studio"
kind = "openai-compatible"
base_url = "https://api.studio.nebius.ai/v1"
api_key_env = "NEBIUS_API_KEY"
credential_name = "nebius"
models = ["deepseek-ai/DeepSeek-V4-Pro", "Qwen/Qwen3-Coder-480B-A35B-Instruct"]
default_model = "deepseek-ai/DeepSeek-V4-Pro"
docs_url = "https://studio.nebius.ai/docs"
thinking_levels = ["off", "low", "medium", "high"]
thinking_models = ["deepseek-ai/DeepSeek-V4-Pro"]
thinking_default = "medium"
thinking_parameter = "reasoning_effort"

[providers.context_windows]
"deepseek-ai/DeepSeek-V4-Pro" = 163840
"""


def _write_user_catalog(tau_home: Path, body: str) -> TauPaths:
    paths = TauPaths(home=tau_home)
    tau_home.mkdir(parents=True, exist_ok=True)
    user_catalog_path(paths).write_text(f"schema_version = 1\n{body}", encoding="utf-8")
    return paths


def test_builtin_catalog_matches_expected_providers() -> None:
    names = [entry.name for entry in BUILTIN_PROVIDER_CATALOG]
    assert names == ["openai", "openai-codex", "anthropic", "openrouter", "huggingface"]


def test_builtin_catalog_golden_anthropic_entry() -> None:
    entry = builtin_provider_entry("anthropic")
    assert entry is not None
    assert entry.display_name == "Anthropic"
    assert entry.kind == "anthropic"
    assert entry.base_url == "https://api.anthropic.com/v1"
    assert entry.api_key_env == "ANTHROPIC_API_KEY"
    assert entry.credential_name == "anthropic"
    assert entry.models == ("claude-sonnet-4-6", "claude-opus-4-8", "claude-haiku-4-5")
    assert entry.default_model == "claude-sonnet-4-6"
    assert entry.docs_url == "https://docs.anthropic.com"
    assert entry.context_windows == {
        "claude-sonnet-4-6": 1_000_000,
        "claude-opus-4-8": 200_000,
        "claude-haiku-4-5": 200_000,
    }
    assert entry.thinking_levels == ("off", "minimal", "low", "medium", "high", "xhigh")
    assert entry.thinking_models == ("claude-sonnet-4-6", "claude-opus-4-8")
    assert entry.thinking_default == "medium"
    assert entry.thinking_parameter == "anthropic.thinking"


def test_builtin_catalog_entries_are_internally_consistent() -> None:
    for entry in builtin_catalog():
        assert entry.default_model in entry.models
        assert set(entry.thinking_models) <= set(entry.models)
        assert set(entry.context_windows or {}) <= set(entry.models)
        if entry.thinking_default is not None:
            assert entry.thinking_levels is not None
            assert entry.thinking_default in entry.thinking_levels


def test_builtin_catalog_resource_is_packaged() -> None:
    assert "[[providers]]" in builtin_catalog_resource_text()


def test_effective_catalog_without_user_file_is_builtin(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau")
    assert effective_catalog(paths) == builtin_catalog()


def test_user_catalog_adds_new_provider(tmp_path: Path) -> None:
    paths = _write_user_catalog(tmp_path / ".tau", VALID_PROVIDER)
    catalog = effective_catalog(paths)
    assert [entry.name for entry in catalog[:-1]] == [e.name for e in builtin_catalog()]
    entry = catalog[-1]
    assert entry.name == "nebius"
    assert entry.default_model == "deepseek-ai/DeepSeek-V4-Pro"
    assert entry.context_windows == {"deepseek-ai/DeepSeek-V4-Pro": 163_840}
    assert entry.thinking_levels == ("off", "low", "medium", "high")


def test_user_catalog_overlays_builtin_provider(tmp_path: Path) -> None:
    paths = _write_user_catalog(
        tmp_path / ".tau",
        """
[[providers]]
name = "anthropic"
models = ["claude-next-1"]
default_model = "claude-next-1"

[providers.context_windows]
"claude-next-1" = 500000
""",
    )
    entry = next(e for e in effective_catalog(paths) if e.name == "anthropic")
    assert entry.models[0] == "claude-next-1"
    assert "claude-sonnet-4-6" in entry.models
    assert entry.default_model == "claude-next-1"
    assert entry.context_windows is not None
    assert entry.context_windows["claude-next-1"] == 500_000
    assert entry.context_windows["claude-opus-4-8"] == 200_000
    # Untouched fields come from the builtin entry.
    assert entry.base_url == "https://api.anthropic.com/v1"
    assert entry.thinking_parameter == "anthropic.thinking"


def test_user_catalog_thinking_fields_replace_as_group(tmp_path: Path) -> None:
    paths = _write_user_catalog(
        tmp_path / ".tau",
        """
[[providers]]
name = "anthropic"
thinking_levels = ["off", "high"]
thinking_default = "high"
""",
    )
    entry = next(e for e in effective_catalog(paths) if e.name == "anthropic")
    assert entry.thinking_levels == ("off", "high")
    assert entry.thinking_default == "high"
    assert entry.thinking_models == ()
    assert entry.thinking_parameter is None


def test_user_catalog_rejects_unknown_keys(tmp_path: Path) -> None:
    paths = _write_user_catalog(tmp_path / ".tau", VALID_PROVIDER.replace("docs_url", "docs_ur1"))
    with pytest.raises(CatalogError, match=r"providers\.nebius"):
        effective_catalog(paths)


def test_user_catalog_rejects_default_model_not_in_models(tmp_path: Path) -> None:
    paths = _write_user_catalog(
        tmp_path / ".tau",
        VALID_PROVIDER.replace(
            'default_model = "deepseek-ai/DeepSeek-V4-Pro"', 'default_model = "missing"'
        ),
    )
    with pytest.raises(CatalogError, match=r"providers\.nebius\.default_model"):
        effective_catalog(paths)


def test_user_catalog_rejects_bad_kind(tmp_path: Path) -> None:
    paths = _write_user_catalog(
        tmp_path / ".tau", VALID_PROVIDER.replace("openai-compatible", "grpc")
    )
    with pytest.raises(CatalogError, match="kind"):
        effective_catalog(paths)


def test_user_catalog_rejects_malformed_toml(tmp_path: Path) -> None:
    paths = _write_user_catalog(tmp_path / ".tau", "[[providers]\nname =")
    with pytest.raises(CatalogError, match="invalid TOML"):
        effective_catalog(paths)


def test_user_catalog_provider_appears_in_settings(tmp_path: Path) -> None:
    paths = _write_user_catalog(tmp_path / ".tau", VALID_PROVIDER)
    settings = load_provider_settings(paths)
    provider = settings.get_provider("nebius")
    assert provider.base_url == "https://api.studio.nebius.ai/v1"
    assert provider.default_model == "deepseek-ai/DeepSeek-V4-Pro"


def test_user_catalog_provider_appears_with_existing_settings_file(tmp_path: Path) -> None:
    paths = _write_user_catalog(tmp_path / ".tau", VALID_PROVIDER)
    (tmp_path / ".tau" / "providers.json").write_text(
        '{"default_provider": "openai", "providers": [{"type": "openai-compatible", '
        '"name": "openai", "base_url": "https://api.openai.com/v1", '
        '"api_key_env": "OPENAI_API_KEY", "models": ["gpt-5.5"], '
        '"default_model": "gpt-5.5"}], "scoped_models": []}',
        encoding="utf-8",
    )
    settings = load_provider_settings(paths)
    assert settings.get_provider("nebius").models[0] == "deepseek-ai/DeepSeek-V4-Pro"
