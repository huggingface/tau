import json
from dataclasses import replace
from pathlib import Path

import pytest

from tau_coding.catalog_loader import CatalogError, save_user_catalog_entries
from tau_coding.paths import TauPaths
from tau_coding.provider_catalog import builtin_provider_entry
from tau_coding.provider_config import (
    OpenAICompatibleProviderConfig,
    ProviderConfigError,
    configured_huggingface_inference_provider,
    load_provider_settings,
    provider_settings_from_json,
    save_default_provider_model,
    save_provider_settings,
    upsert_openai_compatible_provider,
)


def _preferences(route: object, provider: str = "huggingface") -> dict[str, object]:
    return {
        "schema_version": 2,
        "default_provider": provider,
        "provider_preferences": {provider: {"default_inference_provider": route}},
        "scoped_models": [],
    }


def test_bundled_default_and_per_model_override(tmp_path: Path) -> None:
    settings = load_provider_settings(TauPaths(home=tmp_path))
    provider = settings.get_provider("huggingface")
    assert isinstance(provider, OpenAICompatibleProviderConfig)
    assert provider.default_inference_provider == "openrouter"
    assert (
        configured_huggingface_inference_provider(provider, provider.default_model) == "openrouter"
    )
    provider = replace(provider, inference_providers={provider.default_model: "deepinfra"})
    assert (
        configured_huggingface_inference_provider(provider, provider.default_model) == "deepinfra"
    )
    assert (
        configured_huggingface_inference_provider(settings.get_provider("openrouter"), "any")
        is None
    )


@pytest.mark.parametrize("route", ["openrouter", " novita ", None])
def test_default_route_preference_round_trip_including_null(
    tmp_path: Path, route: str | None
) -> None:
    paths = TauPaths(home=tmp_path)
    settings = provider_settings_from_json(_preferences(route), paths=paths)
    provider = settings.get_provider("huggingface")
    expected = route.strip() if route is not None else None
    assert isinstance(provider, OpenAICompatibleProviderConfig)
    assert provider.default_inference_provider == expected
    assert configured_huggingface_inference_provider(provider, provider.default_model) == expected
    save_provider_settings(settings, paths=paths)
    # Saving unrelated preferences must not erase a user's explicit null.
    save_default_provider_model(
        provider_name="huggingface", model=provider.default_model, paths=paths
    )
    saved = json.loads((tmp_path / "providers.json").read_text())
    assert saved["provider_preferences"]["huggingface"]["default_inference_provider"] == expected
    restored = load_provider_settings(paths).get_provider("huggingface")
    assert isinstance(restored, OpenAICompatibleProviderConfig)
    assert restored.default_inference_provider == expected


@pytest.mark.parametrize(
    "route", ["", "bad:route", "with spaces", "fastest", "cheapest", "preferred", 42, True, {}]
)
def test_invalid_default_route_preference_is_rejected(tmp_path: Path, route: object) -> None:
    with pytest.raises(ProviderConfigError):
        provider_settings_from_json(_preferences(route), paths=TauPaths(home=tmp_path))


@pytest.mark.parametrize("provider", ["openai", "anthropic", "openai-codex"])
@pytest.mark.parametrize("route", ["openrouter", None])
def test_default_route_preference_is_huggingface_only(
    tmp_path: Path, provider: str, route: str | None
) -> None:
    with pytest.raises(ProviderConfigError, match="only supported for the huggingface"):
        provider_settings_from_json(_preferences(route, provider), paths=TauPaths(home=tmp_path))


@pytest.mark.parametrize("route", ["novita", None])
def test_provider_update_preserves_default_route(tmp_path: Path, route: str | None) -> None:
    settings = provider_settings_from_json(_preferences(route), paths=TauPaths(home=tmp_path))
    original = settings.get_provider("huggingface")
    assert isinstance(original, OpenAICompatibleProviderConfig)
    updated = upsert_openai_compatible_provider(
        settings, replace(original, default_inference_provider="openrouter")
    ).get_provider("huggingface")
    assert isinstance(updated, OpenAICompatibleProviderConfig)
    assert updated.default_inference_provider == route


@pytest.mark.parametrize("explicit_null", [False, True])
@pytest.mark.parametrize("catalog_default", ["openrouter", "novita"])
def test_legacy_settings_inherit_default_unless_explicitly_disabled(
    tmp_path: Path, explicit_null: bool, catalog_default: str
) -> None:
    paths = TauPaths(home=tmp_path)
    entry = builtin_provider_entry("huggingface")
    assert entry is not None
    save_user_catalog_entries([replace(entry, default_inference_provider=catalog_default)], paths)
    provider = load_provider_settings(paths).get_provider("huggingface")
    assert isinstance(provider, OpenAICompatibleProviderConfig)
    raw = provider.to_json()
    if explicit_null:
        raw["default_inference_provider"] = None
    else:
        raw.pop("default_inference_provider")
    (tmp_path / "providers.json").write_text(
        json.dumps({"default_provider": "huggingface", "providers": [raw]})
    )
    restored = load_provider_settings(paths).get_provider("huggingface")
    assert isinstance(restored, OpenAICompatibleProviderConfig)
    assert restored.default_inference_provider == (None if explicit_null else catalog_default)


def test_catalog_route_default_round_trip(tmp_path: Path) -> None:
    entry = builtin_provider_entry("huggingface")
    assert entry is not None
    paths = TauPaths(home=tmp_path)
    save_user_catalog_entries([replace(entry, default_inference_provider="novita")], paths)
    provider = load_provider_settings(paths).get_provider("huggingface")
    assert isinstance(provider, OpenAICompatibleProviderConfig)
    assert provider.default_inference_provider == "novita"


@pytest.mark.parametrize("route", ["bad:route", "fastest"])
def test_catalog_rejects_invalid_route_default(tmp_path: Path, route: str) -> None:
    entry = builtin_provider_entry("huggingface")
    assert entry is not None
    paths = TauPaths(home=tmp_path)
    save_user_catalog_entries([replace(entry, default_inference_provider=route)], paths)
    with pytest.raises(CatalogError):
        load_provider_settings(paths)


def test_catalog_rejects_non_huggingface_route_default(tmp_path: Path) -> None:
    entry = builtin_provider_entry("openai")
    assert entry is not None
    paths = TauPaths(home=tmp_path)
    save_user_catalog_entries([replace(entry, default_inference_provider="openrouter")], paths)
    with pytest.raises(CatalogError, match="only supported for the huggingface"):
        load_provider_settings(paths)
