"""Import best-effort Pi configuration into Tau's provider settings."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tau_ai import DEFAULT_ANTHROPIC_BASE_URL
from tau_ai.env import DEFAULT_OPENAI_COMPATIBLE_BASE_URL
from tau_coding.paths import TauPaths
from tau_coding.provider_config import (
    AnthropicProviderConfig,
    OpenAICompatibleProviderConfig,
    ProviderConfig,
    ProviderSettings,
    load_provider_settings,
    provider_settings_path,
    save_provider_settings,
    set_default_provider_model,
    upsert_provider,
)

PI_DEFAULT_CONFIG_CANDIDATES = (
    Path("~/.pi/config.json"),
    Path("~/.pi/config.toml"),
    Path("~/.config/pi/config.json"),
    Path("~/.config/pi/config.toml"),
)

_PROVIDER_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "openai-compatible": "openai",
    "openai_compatible": "openai",
}

_API_KEY_ENV_BY_PROVIDER = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}

_BASE_URL_BY_PROVIDER = {
    "anthropic": DEFAULT_ANTHROPIC_BASE_URL,
    "openai": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
}

_DEFAULT_MODEL_BY_PROVIDER = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-5.4",
}


class PiConfigImportError(ValueError):
    """Raised when Pi configuration cannot be imported."""


@dataclass(frozen=True, slots=True)
class PiConfigImportPlan:
    """A dry-run friendly import result."""

    source: Path
    settings: ProviderSettings
    warnings: tuple[str, ...] = ()
    imported_providers: tuple[str, ...] = ()

    def to_json_text(self) -> str:
        """Return the Tau providers.json payload this import would write."""
        return json.dumps(self.settings.to_json(), indent=2, sort_keys=True) + "\n"


@dataclass(slots=True)
class _ImportState:
    warnings: list[str] = field(default_factory=list)
    imported_providers: list[str] = field(default_factory=list)


def default_pi_config_path() -> Path | None:
    """Return the first existing known Pi config path, if any."""
    for candidate in PI_DEFAULT_CONFIG_CANDIDATES:
        path = candidate.expanduser()
        if path.exists():
            return path
    return None


def plan_pi_config_import(
    source: Path,
    *,
    paths: TauPaths | None = None,
    base_settings: ProviderSettings | None = None,
) -> PiConfigImportPlan:
    """Build the Tau provider settings produced by importing a Pi config."""
    resolved_paths = paths or TauPaths()
    resolved_source = _resolve_source(source)
    raw = _load_config_object(resolved_source)
    state = _ImportState()
    providers = _providers_from_pi_config(raw, state)
    if not providers:
        raise PiConfigImportError("Pi config did not contain an importable provider/model")

    settings = base_settings or load_provider_settings(resolved_paths)
    default_provider = _optional_string(_first_present(raw, "provider", "default_provider"))
    default_provider = _normalize_provider_name(default_provider) if default_provider else None
    default_model = _optional_string(_first_present(raw, "model", "default_model"))

    updated = settings
    for provider in providers:
        set_default = provider.name == default_provider or len(providers) == 1
        updated = upsert_provider(updated, provider, set_default=set_default)
        updated = set_default_provider_model(
            updated,
            provider_name=provider.name,
            model=provider.default_model,
        )
        state.imported_providers.append(provider.name)

    if default_provider is not None and default_provider not in {item.name for item in providers}:
        state.warnings.append(f"Default provider {default_provider!r} was not importable")
    if default_model is not None and default_provider is None and len(providers) != 1:
        state.warnings.append("Default model was present but no unambiguous provider was found")

    return PiConfigImportPlan(
        source=resolved_source,
        settings=updated,
        warnings=tuple(state.warnings),
        imported_providers=tuple(dict.fromkeys(state.imported_providers)),
    )


def import_pi_config(
    source: Path,
    *,
    paths: TauPaths | None = None,
    overwrite_existing: bool = False,
) -> tuple[PiConfigImportPlan, Path]:
    """Import Pi config and persist Tau provider settings.

    Existing ``providers.json`` is protected unless ``overwrite_existing`` is true.
    """
    resolved_paths = paths or TauPaths()
    destination = provider_settings_path(resolved_paths)
    if destination.exists() and not overwrite_existing:
        raise PiConfigImportError(
            f"Tau provider settings already exist at {destination}; rerun with --yes to update them"
        )
    plan = plan_pi_config_import(source, paths=resolved_paths)
    written = save_provider_settings(plan.settings, resolved_paths)
    return plan, written


def _resolve_source(source: Path) -> Path:
    path = source.expanduser()
    if path.is_dir():
        for name in ("config.json", "config.toml", "settings.json", "settings.toml"):
            candidate = path / name
            if candidate.exists():
                return candidate
        raise PiConfigImportError(
            f"Pi config directory does not contain a supported config file: {path}"
        )
    if not path.exists():
        raise PiConfigImportError(f"Pi config does not exist: {path}")
    return path


def _load_config_object(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = tomllib.loads(text) if path.suffix.lower() == ".toml" else json.loads(text)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        raise PiConfigImportError(f"Invalid Pi config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PiConfigImportError("Pi config root must be an object")
    return data


def _providers_from_pi_config(data: dict[str, Any], state: _ImportState) -> list[ProviderConfig]:
    raw_providers = data.get("providers")
    providers: list[ProviderConfig] = []
    if isinstance(raw_providers, list):
        for item in raw_providers:
            if isinstance(item, dict):
                provider = _provider_from_mapping(item, state)
                if provider is not None:
                    providers.append(provider)
            else:
                state.warnings.append("Skipped non-object entry in Pi providers list")
    elif isinstance(raw_providers, dict):
        for name, item in raw_providers.items():
            if isinstance(item, dict):
                provider = _provider_from_mapping({"name": name, **item}, state)
                if provider is not None:
                    providers.append(provider)
            else:
                state.warnings.append(f"Skipped non-object Pi provider entry: {name}")

    root_provider = _provider_from_mapping(data, state, allow_missing_name=True)
    if root_provider is not None and root_provider.name not in {
        provider.name for provider in providers
    }:
        providers.append(root_provider)

    for key in sorted(set(data) - _KNOWN_ROOT_KEYS):
        state.warnings.append(f"Pi config field was not imported: {key}")
    return providers


def _provider_from_mapping(
    data: dict[str, Any],
    state: _ImportState,
    *,
    allow_missing_name: bool = False,
) -> ProviderConfig | None:
    provider_name = _optional_string(_first_present(data, "name", "provider", "id"))
    if provider_name is None:
        if not allow_missing_name:
            state.warnings.append("Skipped Pi provider without a name")
        return None
    provider_name = _normalize_provider_name(provider_name)

    model = _optional_string(_first_present(data, "model", "default_model", "defaultModel"))
    if model is None:
        model = _DEFAULT_MODEL_BY_PROVIDER.get(provider_name)
        state.warnings.append(f"Provider {provider_name!r} had no model; using {model!r}")
    if model is None:
        state.warnings.append(f"Skipped provider {provider_name!r} because it has no model")
        return None

    base_url = _optional_string(_first_present(data, "base_url", "baseURL", "baseUrl", "api_base"))
    base_url = (
        base_url or _BASE_URL_BY_PROVIDER.get(provider_name, DEFAULT_OPENAI_COMPATIBLE_BASE_URL)
    ).rstrip("/")
    api_key_env = _optional_string(
        _first_present(data, "api_key_env", "apiKeyEnv", "api_key_env_var")
    )
    api_key_env = api_key_env or _API_KEY_ENV_BY_PROVIDER.get(provider_name, "OPENAI_API_KEY")

    if _first_present(data, "api_key", "apiKey", "key") is not None:
        state.warnings.append(
            f"Provider {provider_name!r} includes a raw API key; "
            f"Tau imported only env var {api_key_env}"
        )

    if provider_name == "anthropic":
        return AnthropicProviderConfig(
            name="anthropic",
            base_url=base_url,
            api_key_env=api_key_env,
            credential_name="anthropic",
            models=(model,),
            default_model=model,
        )
    return OpenAICompatibleProviderConfig(
        name=provider_name,
        base_url=base_url,
        api_key_env=api_key_env,
        credential_name=None if provider_name == "openai" else provider_name,
        models=(model,),
        default_model=model,
    )


def _normalize_provider_name(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    return _PROVIDER_ALIASES.get(normalized, normalized)


def _first_present(data: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _optional_string(value: object | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


_KNOWN_ROOT_KEYS = {
    "apiKey",
    "api_key",
    "api_key_env",
    "api_key_env_var",
    "api_base",
    "baseURL",
    "baseUrl",
    "base_url",
    "defaultModel",
    "default_model",
    "default_provider",
    "id",
    "key",
    "model",
    "name",
    "provider",
    "providers",
}
