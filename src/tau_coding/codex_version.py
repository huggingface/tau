"""Runtime resolution and caching of the latest released Codex client version."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from contextlib import suppress
from os import environ
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import httpx

from tau_ai.openai_codex import DEFAULT_OPENAI_CODEX_CLIENT_VERSION
from tau_coding.paths import TauPaths

CODEX_NPM_LATEST_URL = "https://registry.npmjs.org/@openai%2Fcodex/latest"
CODEX_VERSION_STORE_SCHEMA_VERSION = 1
CODEX_VERSION_REFRESH_INTERVAL_SECONDS = 4 * 60 * 60
CODEX_VERSION_REFRESH_TIMEOUT_SECONDS = 5.0
_VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class CodexClientVersionResolver:
    """Resolve a real released Codex version, retaining safe cached fallbacks."""

    def __init__(
        self,
        *,
        paths: TauPaths | None = None,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._paths = paths or TauPaths()
        self._client = client
        self._now = now

    async def __call__(self) -> str:
        """Return the latest known stable Codex version without making discovery fatal."""
        cache = _read_cache(self._paths.codex_version_store_path)
        if environ.get("TAU_OFFLINE") is not None:
            return _latest_known_version(_cached_version(cache))

        current_time = self._now()
        if (
            cache is not None
            and current_time - cache["checked_at"] < CODEX_VERSION_REFRESH_INTERVAL_SECONDS
        ):
            return _latest_known_version(cache["version"])

        owned_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=CODEX_VERSION_REFRESH_TIMEOUT_SECONDS)
        try:
            headers = {"Accept": "application/json", "User-Agent": "tau-codex-version-refresh"}
            if cache is not None and cache.get("etag"):
                headers["If-None-Match"] = cache["etag"]
            response = await client.get(CODEX_NPM_LATEST_URL, headers=headers)
            if response.status_code == 304 and cache is not None:
                cache["checked_at"] = current_time
                _try_write_cache(self._paths.codex_version_store_path, cache)
                return _latest_known_version(cache["version"])
            response.raise_for_status()
            payload = response.json()
            version = _latest_known_version(_parse_version(payload))
            document: dict[str, Any] = {
                "schema_version": CODEX_VERSION_STORE_SCHEMA_VERSION,
                "checked_at": current_time,
                "etag": response.headers.get("etag"),
                "version": version,
            }
            _try_write_cache(self._paths.codex_version_store_path, document)
            return version
        except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError):
            return _latest_known_version(_cached_version(cache))
        finally:
            if owned_client:
                await client.aclose()


def _parse_version(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Codex package metadata must be an object")
    version = payload.get("version")
    if not isinstance(version, str) or _VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("Codex package metadata has an invalid stable version")
    return version


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != CODEX_VERSION_STORE_SCHEMA_VERSION
            or not isinstance(value.get("checked_at"), int | float)
            or not isinstance(value.get("version"), str)
            or _VERSION_PATTERN.fullmatch(value["version"]) is None
            or (value.get("etag") is not None and not isinstance(value.get("etag"), str))
        ):
            return None
        return value
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _cached_version(cache: dict[str, Any] | None) -> str | None:
    return cache["version"] if cache is not None else None


def _latest_known_version(candidate: str | None) -> str:
    if candidate is None:
        return DEFAULT_OPENAI_CODEX_CLIENT_VERSION
    return max(candidate, DEFAULT_OPENAI_CODEX_CLIENT_VERSION, key=_version_key)


def _version_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


def _try_write_cache(path: Path, value: dict[str, Any]) -> None:
    with suppress(OSError):
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
        except OSError:
            if temp_path is not None:
                with suppress(OSError):
                    temp_path.unlink()
            raise
