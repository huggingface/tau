import os

import httpx
import pytest

from tau_ai.http import create_async_client, normalize_proxy_url, normalized_proxy_environment


def test_normalize_proxy_url_converts_generic_socks_scheme() -> None:
    assert normalize_proxy_url("socks://127.0.0.1:1080") == "socks5://127.0.0.1:1080"
    assert normalize_proxy_url("SOCKS://user:pass@proxy.local:1080") == (
        "socks5://user:pass@proxy.local:1080"
    )


def test_normalize_proxy_url_leaves_explicit_schemes_unchanged() -> None:
    assert normalize_proxy_url("socks5://127.0.0.1:1080") == "socks5://127.0.0.1:1080"
    assert normalize_proxy_url("socks5h://127.0.0.1:1080") == "socks5h://127.0.0.1:1080"
    assert normalize_proxy_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080"


def test_normalized_proxy_environment_restores_original_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:1080")

    with normalized_proxy_environment():
        assert os.environ["ALL_PROXY"] == "socks5://127.0.0.1:1080"

    assert os.environ["ALL_PROXY"] == "socks://127.0.0.1:1080"


@pytest.mark.anyio
async def test_create_async_client_accepts_generic_socks_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:1080")

    client = create_async_client(timeout=1)
    try:
        assert isinstance(client, httpx.AsyncClient)
    finally:
        await client.aclose()

    assert os.environ["ALL_PROXY"] == "socks://127.0.0.1:1080"
