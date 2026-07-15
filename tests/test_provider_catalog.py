"""Tests for the TOML-backed provider catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from tau_coding.catalog_loader import (
    CatalogError,
    builtin_catalog,
    builtin_catalog_resource_text,
    effective_catalog,
    save_user_catalog_entries,
    user_catalog_path,
)
from tau_coding.paths import TauPaths
from tau_coding.provider_catalog import (
    BUILTIN_PROVIDER_CATALOG,
    builtin_provider_entry,
    model_cost_for_input_tokens,
)
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
    assert names == [
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
        "opencode-go",
        "opencode",
        "github-copilot",
    ]


def test_builtin_catalog_golden_anthropic_entry() -> None:
    entry = builtin_provider_entry("anthropic")
    assert entry is not None
    assert entry.display_name == "Anthropic"
    assert entry.kind == "anthropic"
    assert entry.base_url == "https://api.anthropic.com"
    assert entry.api_key_env == "ANTHROPIC_API_KEY"
    assert entry.credential_name == "anthropic"
    assert entry.models == (
        "claude-fable-5",
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
        "claude-opus-4-1",
        "claude-opus-4-1-20250805",
        "claude-opus-4-5",
        "claude-opus-4-5-20251101",
        "claude-opus-4-6",
        "claude-opus-4-7",
        "claude-sonnet-4-5",
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-6",
        "claude-sonnet-5",
    )
    assert entry.default_model == "claude-sonnet-4-6"
    assert entry.docs_url == "https://docs.anthropic.com"
    assert entry.context_windows == {
        "claude-fable-5": 1_000_000,
        "claude-haiku-4-5": 200_000,
        "claude-haiku-4-5-20251001": 200_000,
        "claude-opus-4-1": 200_000,
        "claude-opus-4-1-20250805": 200_000,
        "claude-opus-4-5": 200_000,
        "claude-opus-4-5-20251101": 200_000,
        "claude-opus-4-6": 1_000_000,
        "claude-opus-4-7": 1_000_000,
        "claude-sonnet-4-5": 200_000,
        "claude-sonnet-4-5-20250929": 200_000,
        "claude-sonnet-4-6": 1_000_000,
        "claude-sonnet-5": 1_000_000,
    }
    assert entry.thinking_levels == ("off", "minimal", "low", "medium", "high", "xhigh")
    assert entry.thinking_models == ()
    assert entry.thinking_default == "medium"
    assert entry.thinking_parameter == "anthropic.thinking"
    assert entry.auth_methods == ("api_key", "oauth")


def test_builtin_catalog_oauth_and_opencode_auth_methods() -> None:
    codex = builtin_provider_entry("openai-codex")
    copilot = builtin_provider_entry("github-copilot")
    opencode_go = builtin_provider_entry("opencode-go")
    opencode = builtin_provider_entry("opencode")

    assert codex is not None and codex.auth_methods == ("oauth",)
    assert copilot is not None and copilot.auth_methods == ("oauth",)
    assert opencode_go is not None and opencode_go.auth_methods == ("api_key",)
    assert opencode is not None and opencode.auth_methods == ("api_key",)
    assert opencode_go.api_key_env == "OPENCODE_API_KEY"
    assert opencode.api_key_env == "OPENCODE_API_KEY"


def test_builtin_catalog_golden_nvidia_entry() -> None:
    entry = builtin_provider_entry("nvidia")
    assert entry is not None
    assert entry.display_name == "NVIDIA NIM"
    assert entry.kind == "openai-compatible"
    assert entry.base_url == "https://integrate.api.nvidia.com/v1"
    assert entry.api_key_env == "NVIDIA_API_KEY"
    assert entry.credential_name == "nvidia"
    assert entry.models == (
        "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "nvidia/nvidia-nemotron-nano-9b-v2",
        "meta/llama-3.3-70b-instruct",
        "meta/llama-3.1-8b-instruct",
        "deepseek-ai/deepseek-v4-pro",
        "qwen/qwen3.5-122b-a10b",
        "mistralai/mistral-large-2-instruct",
        "openai/gpt-oss-120b",
    )
    assert entry.default_model == "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    assert entry.docs_url == "https://docs.api.nvidia.com/nim"
    assert entry.api == "openai-completions"
    assert entry.context_windows == {
        "nvidia/llama-3.3-nemotron-super-49b-v1.5": 131_072,
        "nvidia/nvidia-nemotron-nano-9b-v2": 128_000,
        "meta/llama-3.3-70b-instruct": 131_072,
        "meta/llama-3.1-8b-instruct": 131_072,
        "deepseek-ai/deepseek-v4-pro": 1_000_000,
        "qwen/qwen3.5-122b-a10b": 262_144,
        "mistralai/mistral-large-2-instruct": 131_072,
        "openai/gpt-oss-120b": 131_072,
    }
    assert entry.thinking_levels == ("off", "minimal", "low", "medium", "high")
    assert entry.thinking_models == ()
    assert entry.thinking_default == "medium"
    assert entry.thinking_parameter == "reasoning_effort"

    default_metadata = entry.model_metadata[entry.default_model]
    assert default_metadata.name == "NVIDIA: Llama 3.3 Nemotron Super 49B V1.5"
    assert default_metadata.reasoning is True
    assert default_metadata.input == ("text",)
    assert default_metadata.context_window == 131_072
    assert default_metadata.max_tokens == 16_384
    assert default_metadata.cost == {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}

    gpt_oss_metadata = entry.model_metadata["openai/gpt-oss-120b"]
    assert gpt_oss_metadata.reasoning is True
    assert gpt_oss_metadata.context_window == 131_072
    assert gpt_oss_metadata.max_tokens == 65_536


def test_builtin_catalog_golden_kimi_entries() -> None:
    moonshot = builtin_provider_entry("moonshotai")
    assert moonshot is not None
    assert moonshot.display_name == "Moonshot AI (Kimi)"
    assert moonshot.default_model == "kimi-k2.7-code"
    assert "kimi-k2.7-code" in moonshot.models
    assert moonshot.context_windows is not None
    assert moonshot.context_windows["kimi-k2.7-code"] == 262_144

    moonshot_cn = builtin_provider_entry("moonshotai-cn")
    assert moonshot_cn is not None
    assert moonshot_cn.default_model == "kimi-k2.7-code"
    assert "kimi-k2.7-code" in moonshot_cn.models
    assert moonshot_cn.context_windows is not None
    assert moonshot_cn.context_windows["kimi-k2.7-code"] == 262_144

    k2_7 = moonshot.model_metadata["kimi-k2.7-code"]
    assert k2_7.name == "Kimi K2.7 Code"
    assert k2_7.reasoning is True
    assert k2_7.input == ("text", "image")
    assert k2_7.context_window == 262_144
    assert k2_7.max_tokens == 32_768
    assert k2_7.thinking_level_map == {
        "off": None,
        "minimal": None,
        "low": None,
        "high": None,
    }

    coding = builtin_provider_entry("kimi-code")
    assert coding is not None
    assert coding.display_name == "Kimi Code subscription"
    assert coding.base_url == "https://api.kimi.com/coding/v1"
    assert coding.api_key_env == "KIMI_CODE_API_KEY"
    assert coding.credential_name == "kimi-code"
    assert coding.models == ("kimi-for-coding",)
    assert coding.default_model == "kimi-for-coding"
    assert coding.context_windows == {"kimi-for-coding": 262_144}

    latest = coding.model_metadata["kimi-for-coding"]
    assert latest.name == "Kimi for Coding (latest)"
    assert latest.reasoning is True
    assert latest.context_window == 262_144


def test_builtin_minimax_m3_has_tiered_pricing() -> None:
    base_cost = {"input": 0.3, "output": 1.2, "cacheRead": 0.06, "cacheWrite": 0}
    long_context_cost = {
        "input": 0.6,
        "output": 2.4,
        "cacheRead": 0.12,
        "cacheWrite": 0,
    }

    for provider_name in ("minimax", "minimax-cn"):
        entry = builtin_provider_entry(provider_name)
        assert entry is not None
        metadata = entry.model_metadata["MiniMax-M3"]
        assert metadata.input == ("text", "image")
        assert metadata.cost == base_cost
        assert model_cost_for_input_tokens(metadata, 512_000) == base_cost
        assert model_cost_for_input_tokens(metadata, 512_001) == long_context_cost


@pytest.mark.parametrize("input_tokens", [-1, True])
def test_model_cost_for_input_tokens_rejects_invalid_count(input_tokens: int) -> None:
    entry = builtin_provider_entry("minimax")
    assert entry is not None
    metadata = entry.model_metadata["MiniMax-M3"]

    with pytest.raises(ValueError, match="non-negative integer"):
        model_cost_for_input_tokens(metadata, input_tokens)


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
    assert entry.context_windows["claude-opus-4-7"] == 1_000_000
    # Untouched fields come from the builtin entry.
    assert entry.base_url == "https://api.anthropic.com"
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


def test_user_catalog_overlays_and_serializes_cost_tiers(tmp_path: Path) -> None:
    paths = _write_user_catalog(
        tmp_path / ".tau",
        """
[[providers]]
name = "minimax"

[providers.model_metadata."MiniMax-M3"]
cost_tiers = [
  { max_input_tokens = 400000, input = 0.2, output = 1.0, cacheRead = 0.04, cacheWrite = 0 },
  { input = 0.5, output = 2.0, cacheRead = 0.1, cacheWrite = 0 },
]
""",
    )
    entry = next(e for e in effective_catalog(paths) if e.name == "minimax")
    metadata = entry.model_metadata["MiniMax-M3"]
    assert model_cost_for_input_tokens(metadata, 400_000) == {
        "input": 0.2,
        "output": 1.0,
        "cacheRead": 0.04,
        "cacheWrite": 0,
    }
    long_context_cost = {
        "input": 0.5,
        "output": 2.0,
        "cacheRead": 0.1,
        "cacheWrite": 0,
    }
    assert model_cost_for_input_tokens(metadata, 400_001) == long_context_cost

    save_user_catalog_entries([entry], paths)
    reloaded = next(e for e in effective_catalog(paths) if e.name == "minimax")
    assert (
        model_cost_for_input_tokens(reloaded.model_metadata["MiniMax-M3"], 400_001)
        == long_context_cost
    )


def test_user_catalog_rejects_bounded_final_cost_tier(tmp_path: Path) -> None:
    paths = _write_user_catalog(
        tmp_path / ".tau",
        """
[[providers]]
name = "minimax"

[providers.model_metadata."MiniMax-M3"]
cost_tiers = [
  { max_input_tokens = 512000, input = 0.3, output = 1.2, cacheRead = 0.06, cacheWrite = 0 },
]
""",
    )
    with pytest.raises(CatalogError, match="final tier must omit max_input_tokens"):
        effective_catalog(paths)


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


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (
            VALID_PROVIDER.replace('display_name = "Nebius AI Studio"', 'display_name = ""'),
            r"providers\.nebius\.display_name",
        ),
        (
            VALID_PROVIDER.replace(
                'models = ["deepseek-ai/DeepSeek-V4-Pro", "Qwen/Qwen3-Coder-480B-A35B-Instruct"]',
                'models = [""]',
            ),
            r"providers\.nebius\.models",
        ),
        (
            VALID_PROVIDER.replace('"deepseek-ai/DeepSeek-V4-Pro" = 163840', '"" = 163840'),
            r"providers\.nebius\.context_windows",
        ),
        (
            VALID_PROVIDER.replace(
                '"deepseek-ai/DeepSeek-V4-Pro" = 163840',
                '"deepseek-ai/DeepSeek-V4-Pro" = 0',
            ),
            r"providers\.nebius\.context_windows",
        ),
        (
            VALID_PROVIDER.replace(
                '"deepseek-ai/DeepSeek-V4-Pro" = 163840',
                '"deepseek-ai/DeepSeek-V4-Pro" = -1',
            ),
            r"providers\.nebius\.context_windows",
        ),
        (
            VALID_PROVIDER.replace(
                '"deepseek-ai/DeepSeek-V4-Pro" = 163840',
                '"deepseek-ai/DeepSeek-V4-Pro" = true',
            ),
            r"providers\.nebius\.context_windows",
        ),
        (
            VALID_PROVIDER.replace(
                '"deepseek-ai/DeepSeek-V4-Pro" = 163840',
                '"deepseek-ai/DeepSeek-V4-Pro" = "163840"',
            ),
            r"providers\.nebius\.context_windows",
        ),
    ],
)
def test_user_catalog_rejects_empty_and_coerced_values(
    tmp_path: Path,
    body: str,
    match: str,
) -> None:
    paths = _write_user_catalog(tmp_path / ".tau", body)
    with pytest.raises(CatalogError, match=match):
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
