from __future__ import annotations

import json
from pathlib import Path

import httpx2
import pytest

import tau_coding.models_dev_store as store
from tau_coding.catalog_loader import effective_catalog
from tau_coding.models_dev import MODELS_DEV_URL, NVIDIA_MODELS_URL
from tau_coding.models_dev_store import (
    ModelsDevRefreshError,
    cached_models_dev_catalog_document,
    refresh_models_dev_catalog,
)
from tau_coding.paths import TauPaths

FIXTURE = Path(__file__).parent / "fixtures/models_dev_catalog.json"


def _source() -> dict[str, object]:
    source = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source["nvidia"] = {"id": "nvidia", "models": {}}
    return source


def _client(source: dict[str, object]) -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == MODELS_DEV_URL:
            return httpx2.Response(200, json=source, headers={"etag": '"fixture"'})
        if str(request.url) == NVIDIA_MODELS_URL:
            return httpx2.Response(200, json={"data": []})
        raise AssertionError(f"unexpected request: {request.url}")

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


@pytest.mark.anyio
async def test_refresh_persists_catalog_and_effective_catalog_uses_newer_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = TauPaths(home=tmp_path / ".tau")
    async with _client(_source()) as client:
        result = await refresh_models_dev_catalog(
            paths=paths,
            force=True,
            client=client,
            now=1000.0,
        )

    assert result.refreshed
    assert result.model_count > 0
    assert result.cache_path.exists()
    monkeypatch.setattr(
        store,
        "bundled_models_dev_catalog_document",
        lambda: {"generated_at": 2_000_000},
    )
    assert cached_models_dev_catalog_document(paths) is None
    monkeypatch.setattr(store, "bundled_models_dev_catalog_document", lambda: {"generated_at": 0})
    assert cached_models_dev_catalog_document(paths) is not None
    huggingface = next(
        provider for provider in effective_catalog(paths) if provider.name == "huggingface"
    )
    assert "example/new-tool-model" in huggingface.models


@pytest.mark.anyio
async def test_refresh_revalidates_with_etag_and_preserves_cached_body(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau")
    async with _client(_source()) as client:
        await refresh_models_dev_catalog(paths=paths, force=True, client=client, now=1000.0)
    result_path = paths.home / "models-store.json"
    before_catalog = json.loads(result_path.read_text(encoding="utf-8"))["catalog"]

    def not_modified(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["if-none-match"] == '"fixture"'
        return httpx2.Response(304)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(not_modified)) as client:
        result = await refresh_models_dev_catalog(
            paths=paths,
            force=True,
            client=client,
            now=2000.0,
        )

    after = json.loads(result_path.read_text(encoding="utf-8"))
    assert result.not_modified
    assert after["checked_at"] == 2000.0
    assert after["catalog"] == before_catalog


@pytest.mark.anyio
async def test_offline_mode_uses_bundled_catalog_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAU_OFFLINE", "1")

    def no_network(_request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("offline mode should avoid network")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(no_network)) as client:
        result = await refresh_models_dev_catalog(
            paths=TauPaths(home=tmp_path / ".tau"),
            force=True,
            client=client,
        )

    assert not result.refreshed
    assert result.model_count > 0


@pytest.mark.anyio
async def test_fresh_cache_skips_network_and_failed_force_preserves_it(tmp_path: Path) -> None:
    paths = TauPaths(home=tmp_path / ".tau")
    async with _client(_source()) as client:
        first = await refresh_models_dev_catalog(paths=paths, force=True, client=client, now=1000.0)
    cached_text = first.cache_path.read_text(encoding="utf-8")

    def no_network(_request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("fresh cache should avoid network")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(no_network)) as client:
        skipped = await refresh_models_dev_catalog(
            paths=paths,
            client=client,
            now=1001.0,
        )
    assert not skipped.refreshed

    def failure(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(failure)) as client:
        with pytest.raises(ModelsDevRefreshError, match="503"):
            await refresh_models_dev_catalog(
                paths=paths,
                force=True,
                client=client,
                now=2000.0,
            )
    assert first.cache_path.read_text(encoding="utf-8") == cached_text
