"""Persisted runtime refresh for the models.dev catalog snapshot."""

from __future__ import annotations

import json
import time
from contextlib import suppress
from dataclasses import dataclass
from os import environ
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

import httpx

from tau_coding.models_dev import (
    MODELS_DEV_URL,
    NVIDIA_MODELS_URL,
    bundled_models_dev_catalog_document,
    models_dev_catalog_document,
    models_dev_catalog_overlay,
    nvidia_model_filter,
)
from tau_coding.paths import TauPaths

MODELS_STORE_SCHEMA_VERSION = 1
MODELS_REFRESH_INTERVAL_SECONDS = 4 * 60 * 60
MODELS_REFRESH_TIMEOUT_SECONDS = 15.0


class ModelsDevRefreshError(RuntimeError):
    """Raised when a forced/runtime catalog refresh cannot complete."""


@dataclass(frozen=True, slots=True)
class ModelsDevRefreshResult:
    refreshed: bool
    not_modified: bool
    model_count: int
    cache_path: Path


def models_store_path(paths: TauPaths | None = None) -> Path:
    return (paths or TauPaths()).models_store_path


def cached_models_dev_catalog_document(
    paths: TauPaths | None = None,
) -> dict[str, Any] | None:
    """Return a valid cache only when it is newer than the bundled snapshot."""
    cache = _read_cache(paths)
    if cache is None:
        return None
    bundled = bundled_models_dev_catalog_document() or {}
    bundled_at = bundled.get("generated_at")
    cached_at = cache["catalog"].get("generated_at")
    if isinstance(bundled_at, int) and isinstance(cached_at, int) and cached_at <= bundled_at:
        return None
    return cast(dict[str, Any], cache["catalog"])


def cached_models_dev_catalog_overlay(paths: TauPaths | None = None) -> dict[str, Any] | None:
    document = cached_models_dev_catalog_document(paths)
    return models_dev_catalog_overlay(document) if document is not None else None


async def refresh_models_dev_catalog(
    *,
    paths: TauPaths | None = None,
    force: bool = False,
    client: httpx.AsyncClient | None = None,
    now: float | None = None,
) -> ModelsDevRefreshResult:
    """Refresh models.dev plus Pi's NVIDIA filter and atomically cache the result."""
    resolved_paths = paths or TauPaths()
    path = models_store_path(resolved_paths)
    current_time = now if now is not None else time.time()
    cache = _read_cache(resolved_paths)
    if environ.get("TAU_OFFLINE") is not None:
        document = cache["catalog"] if cache is not None else bundled_models_dev_catalog_document()
        return ModelsDevRefreshResult(
            refreshed=False,
            not_modified=False,
            model_count=_model_count(document) if isinstance(document, dict) else 0,
            cache_path=path,
        )
    if (
        not force
        and cache is not None
        and current_time - cache["checked_at"] < MODELS_REFRESH_INTERVAL_SECONDS
    ):
        return ModelsDevRefreshResult(
            refreshed=False,
            not_modified=False,
            model_count=_model_count(cache["catalog"]),
            cache_path=path,
        )

    owned_client = client is None
    http = client or httpx.AsyncClient(timeout=MODELS_REFRESH_TIMEOUT_SECONDS)
    try:
        headers = {"Accept": "application/json", "User-Agent": "tau-model-catalog-refresh"}
        if cache is not None and cache.get("etag"):
            headers["If-None-Match"] = cache["etag"]
        response = await http.get(MODELS_DEV_URL, headers=headers)
        if response.status_code == 304 and cache is not None:
            cache["checked_at"] = current_time
            _write_cache(path, cache)
            return ModelsDevRefreshResult(
                refreshed=False,
                not_modified=True,
                model_count=_model_count(cache["catalog"]),
                cache_path=path,
            )
        response.raise_for_status()
        source = response.json()

        nvidia_response = await http.get(
            NVIDIA_MODELS_URL,
            headers={"Accept": "application/json", "User-Agent": "tau-model-catalog-refresh"},
        )
        nvidia_response.raise_for_status()
        nvidia_source = nvidia_response.json()

        # Imported here to avoid a catalog-loader import cycle during package startup.
        from tau_coding.catalog_loader import builtin_source_catalog

        document = models_dev_catalog_document(
            source,
            builtin_source_catalog(),
            provider_model_filters={"nvidia": nvidia_model_filter(source, nvidia_source)},
            generated_at=int(current_time * 1000),
        )
        # Validate before persistence so malformed upstream data cannot poison startup.
        models_dev_catalog_overlay(document)
        cache_document: dict[str, Any] = {
            "schema_version": MODELS_STORE_SCHEMA_VERSION,
            "checked_at": current_time,
            "etag": response.headers.get("etag"),
            "catalog": document,
        }
        _write_cache(path, cache_document)
        return ModelsDevRefreshResult(
            refreshed=True,
            not_modified=False,
            model_count=_model_count(document),
            cache_path=path,
        )
    except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ModelsDevRefreshError(f"Could not refresh model catalogs: {error}") from error
    finally:
        if owned_client:
            await http.aclose()


def _read_cache(paths: TauPaths | None) -> dict[str, Any] | None:
    path = models_store_path(paths)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != MODELS_STORE_SCHEMA_VERSION
        ):
            return None
        checked_at = value.get("checked_at")
        catalog = value.get("catalog")
        if not isinstance(checked_at, int | float) or not isinstance(catalog, dict):
            return None
        models_dev_catalog_overlay(catalog)
        return value
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _model_count(document: dict[str, Any]) -> int:
    providers = document.get("providers")
    if not isinstance(providers, dict):
        return 0
    return sum(
        len(provider.get("models", []))
        for provider in providers.values()
        if isinstance(provider, dict) and isinstance(provider.get("models"), list)
    )


def _write_cache(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile(
            "w",
            dir=path.parent,
            encoding="utf-8",
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(value, temp_file, indent=2, sort_keys=True)
            temp_file.write("\n")
            temp_file.flush()
        temp_path.replace(path)
    except Exception:
        if temp_path is not None:
            with suppress(OSError):
                temp_path.unlink()
        raise
