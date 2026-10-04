import json

import httpx
import pytest

from tau_ai.openai_codex import DEFAULT_OPENAI_CODEX_CLIENT_VERSION
from tau_coding.codex_version import (
    CODEX_NPM_LATEST_URL,
    CodexClientVersionResolver,
)
from tau_coding.paths import TauPaths


@pytest.mark.anyio
async def test_codex_version_resolver_fetches_and_caches_latest_release(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"version": "0.153.4"}, headers={"etag": '"latest"'})

    paths = TauPaths(home=tmp_path / ".tau")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = CodexClientVersionResolver(paths=paths, client=client, now=lambda: 1_000)
        assert await resolver() == "0.153.4"
        assert await resolver() == "0.153.4"

    assert len(requests) == 1
    assert str(requests[0].url) == CODEX_NPM_LATEST_URL
    assert json.loads(paths.codex_version_store_path.read_text(encoding="utf-8")) == {
        "checked_at": 1_000,
        "etag": '"latest"',
        "schema_version": 1,
        "version": "0.153.4",
    }


@pytest.mark.anyio
async def test_codex_version_resolver_revalidates_stale_cache(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(304)

    paths = TauPaths(home=tmp_path / ".tau")
    paths.home.mkdir(parents=True)
    paths.codex_version_store_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "checked_at": 1,
                "etag": '"cached"',
                "version": "0.154.0",
            }
        ),
        encoding="utf-8",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = CodexClientVersionResolver(paths=paths, client=client, now=lambda: 20_000)
        assert await resolver() == "0.154.0"

    assert requests[0].headers["if-none-match"] == '"cached"'
    cache = json.loads(paths.codex_version_store_path.read_text(encoding="utf-8"))
    assert cache["checked_at"] == 20_000


@pytest.mark.anyio
async def test_codex_version_resolver_uses_stale_cache_on_failure(tmp_path) -> None:
    paths = TauPaths(home=tmp_path / ".tau")
    paths.home.mkdir(parents=True)
    paths.codex_version_store_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "checked_at": 1,
                "etag": None,
                "version": "0.154.0",
            }
        ),
        encoding="utf-8",
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = CodexClientVersionResolver(paths=paths, client=client, now=lambda: 20_000)
        assert await resolver() == "0.154.0"


@pytest.mark.anyio
async def test_codex_version_resolver_rejects_malformed_version(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "999.999.999-next.1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = CodexClientVersionResolver(paths=TauPaths(home=tmp_path / ".tau"), client=client)
        assert await resolver() == DEFAULT_OPENAI_CODEX_CLIENT_VERSION


@pytest.mark.anyio
async def test_codex_version_resolver_offline_uses_cache_without_network(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("TAU_OFFLINE", "1")
    paths = TauPaths(home=tmp_path / ".tau")
    paths.home.mkdir(parents=True)
    paths.codex_version_store_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "checked_at": 1,
                "etag": None,
                "version": "0.154.0",
            }
        ),
        encoding="utf-8",
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("offline resolution must not use the network")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = CodexClientVersionResolver(paths=paths, client=client)
        assert await resolver() == "0.154.0"
