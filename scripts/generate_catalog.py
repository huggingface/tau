#!/usr/bin/env python3
"""Generate Tau's committed model catalog from models.dev plus Tau overlays."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx

from tau_coding.catalog_loader import _catalog_to_toml, _entries_from_raw

SCHEMA_VERSION = 1
SOURCE_URL = "https://models.dev/api.json"
USER_AGENT = "tau-catalog-generator/1"
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "src/tau_coding/data"
CATALOG_PATH = DATA_DIR / "catalog.toml"
OVERLAY_PATH = DATA_DIR / "catalog_overrides.toml"
MANIFEST_PATH = DATA_DIR / "catalog_manifest.json"

# Provider identity and authentication remain in catalog_overrides.toml. This map only
# connects Tau provider names to models.dev metadata sources.
PROVIDER_SOURCES = {
    "anthropic": "anthropic",
    "cerebras": "cerebras",
    "deepseek": "deepseek",
    "fireworks": "fireworks-ai",
    "github-copilot": "github-copilot",
    "google": "google",
    "groq": "groq",
    "huggingface": "huggingface",
    "minimax": "minimax",
    "minimax-cn": "minimax-cn",
    "mistral": "mistral",
    "moonshotai": "moonshotai",
    "moonshotai-cn": "moonshotai-cn",
    "nvidia": "nvidia",
    "openai": "openai",
    "opencode": "opencode",
    "opencode-go": "opencode-go",
    "openrouter": "openrouter",
    "together": "togetherai",
    "vercel-ai-gateway": "vercel",
    "xai": "xai",
    "xiaomi": "xiaomi",
    "xiaomi-token-plan-ams": "xiaomi-token-plan-ams",
    "xiaomi-token-plan-cn": "xiaomi-token-plan-cn",
    "xiaomi-token-plan-sgp": "xiaomi-token-plan-sgp",
    "zai": "zai",
}

# Exact upstream sets are intentionally enforced first for the providers that motivated
# issue #571. More providers can be enrolled after their broader model selections are reviewed.
STRICT_PROVIDERS = ("anthropic", "github-copilot", "openai")


class CatalogGenerationError(RuntimeError):
    """Raised when upstream data or a generated catalog fails integrity checks."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def _load_manifest() -> dict[str, Any]:
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise CatalogGenerationError(f"Cannot read {MANIFEST_PATH}: {error}") from error


def fetch_upstream() -> dict[str, Any]:
    """Fetch the models.dev provider payload."""
    try:
        response = httpx.get(
            SOURCE_URL,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=60,
        )
        response.raise_for_status()
        value = response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise CatalogGenerationError(f"Could not fetch {SOURCE_URL}: {error}") from error
    if not isinstance(value, dict):
        raise CatalogGenerationError("models.dev returned a non-object root")
    return value


def _tool_model_ids(upstream: dict[str, Any], source_name: str) -> list[str]:
    source = upstream.get(source_name)
    if not isinstance(source, dict) or not isinstance(source.get("models"), dict):
        raise CatalogGenerationError(f"models.dev has no model map for {source_name!r}")
    return sorted(
        model_id
        for model_id, model in source["models"].items()
        if isinstance(model_id, str) and isinstance(model, dict) and model.get("tool_call") is True
    )


def validate_strict_sets(upstream: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Reject unreviewed upstream model additions and removals."""
    expected_sets = manifest.get("expected_tool_model_ids")
    if not isinstance(expected_sets, dict):
        raise CatalogGenerationError("Manifest has no expected_tool_model_ids")
    errors: list[str] = []
    for provider_name in STRICT_PROVIDERS:
        source_name = PROVIDER_SOURCES[provider_name]
        actual = set(_tool_model_ids(upstream, source_name))
        expected_value = expected_sets.get(provider_name)
        if not isinstance(expected_value, list) or not all(
            isinstance(item, str) for item in expected_value
        ):
            errors.append(f"{provider_name}: manifest allowlist is missing")
            continue
        expected = set(expected_value)
        added = sorted(actual - expected)
        removed = sorted(expected - actual)
        if added or removed:
            errors.append(f"{provider_name}: added={added!r}, removed={removed!r}")
    if errors:
        details = "\n".join(errors)
        raise CatalogGenerationError(
            "models.dev model IDs drifted; review them, then rerun with "
            f"--update-allowlist:\n{details}"
        )


def metadata_from_upstream(model: dict[str, Any]) -> dict[str, Any]:
    """Map one models.dev model into Tau's model_metadata shape."""
    limit = model.get("limit")
    cost = model.get("cost")
    modalities = model.get("modalities")
    if not isinstance(limit, dict) or not isinstance(cost, dict):
        raise CatalogGenerationError(f"Upstream model {model.get('id')!r} lacks limits or cost")
    if not isinstance(modalities, dict) or not isinstance(modalities.get("input"), list):
        raise CatalogGenerationError(f"Upstream model {model.get('id')!r} lacks input modalities")

    context = limit.get("context")
    output = limit.get("output")
    if not isinstance(context, int) or context <= 0 or not isinstance(output, int) or output <= 0:
        raise CatalogGenerationError(f"Upstream model {model.get('id')!r} has invalid limits")
    for field in ("input", "output"):
        if not isinstance(cost.get(field), int | float) or cost[field] < 0:
            raise CatalogGenerationError(f"Upstream model {model.get('id')!r} lacks cost.{field}")

    input_modalities = modalities["input"]
    return {
        "name": model.get("name") or model.get("id"),
        "reasoning": model.get("reasoning") is True,
        "input": ["text", "image"] if "image" in input_modalities else ["text"],
        "cost": {
            "input": cost["input"],
            "output": cost["output"],
            "cacheRead": cost.get("cache_read", 0),
            "cacheWrite": cost.get("cache_write", 0),
        },
        "context_window": context,
        "max_tokens": output,
    }


def _complete_metadata_or_none(model: dict[str, Any]) -> dict[str, Any] | None:
    """Return mapped metadata only when models.dev supplies every required field."""
    try:
        return metadata_from_upstream(model)
    except CatalogGenerationError:
        return None


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def generate_catalog(overlays: dict[str, Any], upstream: dict[str, Any]) -> dict[str, Any]:
    """Apply generated model metadata underneath Tau's curated overlays."""
    output = deepcopy(overlays)
    providers = output.get("providers")
    if not isinstance(providers, list):
        raise CatalogGenerationError("catalog_overrides.toml has no providers array")

    for provider in providers:
        if not isinstance(provider, dict) or not isinstance(provider.get("name"), str):
            raise CatalogGenerationError("Invalid provider in catalog_overrides.toml")
        source_name = PROVIDER_SOURCES.get(provider["name"])
        if source_name is None:
            continue
        source = upstream.get(source_name)
        source_models = source.get("models") if isinstance(source, dict) else None
        if not isinstance(source_models, dict):
            raise CatalogGenerationError(f"models.dev has no model map for {source_name!r}")

        overlay_metadata = provider.get("model_metadata", {})
        overlay_contexts = provider.get("context_windows", {})
        if not isinstance(overlay_metadata, dict) or not isinstance(overlay_contexts, dict):
            raise CatalogGenerationError(f"Invalid metadata overlay for {provider['name']}")
        generated_metadata: dict[str, Any] = {}
        generated_contexts: dict[str, int] = {}
        for model_id in provider.get("models", []):
            source_model = source_models.get(model_id)
            model_overlay = overlay_metadata.get(model_id, {})
            if not isinstance(model_overlay, dict):
                raise CatalogGenerationError(f"Invalid metadata for {provider['name']}/{model_id}")
            generated = (
                _complete_metadata_or_none(source_model)
                if isinstance(source_model, dict) and source_model.get("tool_call") is True
                else None
            )
            if generated is not None:
                generated_metadata[model_id] = _deep_merge(generated, model_overlay)
                generated_contexts[model_id] = generated["context_window"]
            elif model_overlay:
                generated_metadata[model_id] = deepcopy(model_overlay)
        provider["model_metadata"] = generated_metadata
        provider["context_windows"] = {**generated_contexts, **overlay_contexts}

    return output


def validate_catalog(catalog: dict[str, Any]) -> None:
    """Validate schema plus generated-field completeness and Anthropic output limits."""
    _entries_from_raw(catalog, source="generated catalog.toml")
    for provider in catalog.get("providers", []):
        models = provider.get("models", [])
        metadata = provider.get("model_metadata", {})
        if set(models) - set(metadata):
            missing = sorted(set(models) - set(metadata))
            raise CatalogGenerationError(f"{provider['name']} lacks metadata for {missing!r}")
        provider_api = provider.get("api")
        for model_id, model in metadata.items():
            effective_api = model.get("api", provider_api)
            if effective_api == "anthropic-messages" and not model.get("max_tokens"):
                raise CatalogGenerationError(
                    f"{provider['name']}/{model_id} uses anthropic-messages without max_tokens"
                )


def _provider_hashes(catalog: dict[str, Any]) -> dict[str, str]:
    return {
        provider["name"]: _sha256_bytes(_canonical_bytes(provider))
        for provider in catalog["providers"]
    }


def build_manifest(
    catalog_text: str,
    catalog: dict[str, Any],
    upstream: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    update_allowlist: bool,
) -> dict[str, Any]:
    expected: dict[str, list[str]] = {}
    old_expected = previous.get("expected_tool_model_ids", {}) if previous else {}
    for provider_name in STRICT_PROVIDERS:
        if update_allowlist or provider_name not in old_expected:
            expected[provider_name] = _tool_model_ids(upstream, PROVIDER_SOURCES[provider_name])
        else:
            expected[provider_name] = old_expected[provider_name]
    return {
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE_URL,
        "source_sha256": _sha256_bytes(_canonical_bytes(upstream)),
        "overlay_sha256": _sha256_bytes(OVERLAY_PATH.read_bytes()),
        "catalog_sha256": _sha256_bytes(catalog_text.encode()),
        "provider_sha256": _provider_hashes(catalog),
        "expected_tool_model_ids": expected,
    }


def check_catalog() -> None:
    """Validate the committed catalog and its manifest without network access."""
    catalog_bytes = CATALOG_PATH.read_bytes()
    catalog = _load_toml(CATALOG_PATH)
    manifest = _load_manifest()
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise CatalogGenerationError("Catalog manifest schema version is unsupported")
    if manifest.get("overlay_sha256") != _sha256_bytes(OVERLAY_PATH.read_bytes()):
        raise CatalogGenerationError("catalog_overrides.toml does not match the manifest")
    if manifest.get("catalog_sha256") != _sha256_bytes(catalog_bytes):
        raise CatalogGenerationError("catalog.toml does not match catalog_manifest.json")
    if manifest.get("provider_sha256") != _provider_hashes(catalog):
        raise CatalogGenerationError("Catalog provider hashes do not match the manifest")
    validate_catalog(catalog)


def write_generated(*, strict: bool, update_allowlist: bool) -> None:
    upstream = fetch_upstream()
    previous = _load_manifest() if MANIFEST_PATH.exists() else None
    if strict and not update_allowlist:
        if previous is None:
            raise CatalogGenerationError("Strict generation requires an existing manifest")
        validate_strict_sets(upstream, previous)
    catalog = generate_catalog(_load_toml(OVERLAY_PATH), upstream)
    validate_catalog(catalog)
    catalog_text = (
        "# Generated by scripts/generate_catalog.py; do not edit directly.\n"
        "# Tau-specific settings live in catalog_overrides.toml.\n\n" + _catalog_to_toml(catalog)
    )
    manifest = build_manifest(
        catalog_text,
        catalog,
        upstream,
        previous,
        update_allowlist=update_allowlist,
    )
    CATALOG_PATH.write_text(catalog_text, encoding="utf-8")
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="validate committed files offline")
    action.add_argument(
        "--generate", action="store_true", help="fetch and regenerate catalog files"
    )
    parser.add_argument("--strict", action="store_true", help="reject upstream model-ID drift")
    parser.add_argument(
        "--update-allowlist",
        action="store_true",
        help="accept current strict-provider model IDs after review",
    )
    args = parser.parse_args()
    try:
        if args.check:
            if args.strict or args.update_allowlist:
                parser.error("--strict/--update-allowlist require --generate")
            check_catalog()
        else:
            write_generated(strict=args.strict, update_allowlist=args.update_allowlist)
    except CatalogGenerationError as error:
        print(f"catalog error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
