import base64
from json import dumps
from time import time
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest

from tau_coding.oauth import (
    OPENAI_CODEX_ACCOUNT_CLAIM,
    OPENAI_CODEX_CLIENT_ID,
    account_id_from_access_token,
    create_openai_codex_authorization_flow,
    parse_authorization_input,
    refresh_openai_codex_token,
)


def test_create_openai_codex_authorization_flow_includes_pkce_and_codex_params() -> None:
    flow = create_openai_codex_authorization_flow(originator="tau-test")

    url = urlparse(flow.url)
    params = parse_qs(url.query)

    assert url.geturl().startswith("https://auth.openai.com/oauth/authorize?")
    assert params["response_type"] == ["code"]
    assert params["client_id"] == [OPENAI_CODEX_CLIENT_ID]
    assert params["redirect_uri"] == ["http://localhost:1455/auth/callback"]
    assert params["scope"] == ["openid profile email offline_access"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["codex_cli_simplified_flow"] == ["true"]
    assert params["originator"] == ["tau-test"]
    assert params["state"] == [flow.state]
    assert params["code_challenge"][0]
    assert flow.verifier


def test_parse_authorization_input_accepts_redirect_url_query_and_raw_code() -> None:
    assert (
        parse_authorization_input("http://localhost:1455/auth/callback?code=abc&state=state-1").code
        == "abc"
    )
    assert parse_authorization_input("code=abc&state=state-1").state == "state-1"
    assert parse_authorization_input("abc#state-1").state == "state-1"
    assert parse_authorization_input("abc").code == "abc"


def test_account_id_from_access_token_reads_openai_auth_claim() -> None:
    assert account_id_from_access_token(_jwt("account-1")) == "account-1"
    assert account_id_from_access_token("not-a-jwt") is None


@pytest.mark.anyio
async def test_refresh_openai_codex_token_returns_oauth_credential() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = request.content.decode()
        assert "grant_type=refresh_token" in body
        assert "client_id=" in body
        return httpx2.Response(
            200,
            json={
                "access_token": _jwt("account-2"),
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        credential = await refresh_openai_codex_token("old-refresh", client=client)

    assert credential.access == _jwt("account-2")
    assert credential.refresh == "new-refresh"
    assert credential.account_id == "account-2"
    assert credential.expires > 0


@pytest.mark.anyio
async def test_refresh_openai_codex_token_preserves_refresh_and_reads_jwt_expiry() -> None:
    expires = int(time()) + 3600
    access_token = _jwt("account-3", expires=expires)

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = request.content.decode()
        assert "grant_type=refresh_token" in body
        return httpx2.Response(200, json={"access_token": access_token})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        credential = await refresh_openai_codex_token("old-refresh", client=client)

    assert credential.access == access_token
    assert credential.refresh == "old-refresh"
    assert credential.account_id == "account-3"
    assert credential.expires == expires * 1000


def _jwt(account_id: str, *, expires: int | None = None) -> str:
    payload = {OPENAI_CODEX_ACCOUNT_CLAIM: {"chatgpt_account_id": account_id}}
    if expires is not None:
        payload["exp"] = expires
    return ".".join(
        [
            _base64url(dumps({"alg": "none"}).encode()),
            _base64url(dumps(payload).encode()),
            "signature",
        ]
    )


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
