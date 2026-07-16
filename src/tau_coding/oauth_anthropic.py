"""Anthropic Claude Pro/Max OAuth provider."""

from __future__ import annotations

import asyncio
import time
import webbrowser
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import httpx

from tau_ai.http import create_async_client
from tau_coding.credentials import OAuthCredential
from tau_coding.oauth import (
    OAuthError,
    _start_local_oauth_server,
    create_pkce_pair,
    oauth_credential_is_expired,
    parse_authorization_input,
)
from tau_coding.oauth_types import (
    OAuthAuthInfo,
    OAuthFlowKind,
    OAuthLoginCallbacks,
    OAuthPrompt,
    OAuthRuntimeAuth,
)

ANTHROPIC_OAUTH_PROVIDER = "anthropic"
ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944a1962f5e"
ANTHROPIC_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
ANTHROPIC_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
ANTHROPIC_REDIRECT_URI = "http://localhost:53692/callback"
ANTHROPIC_SCOPE = (
    "org:create_api_key user:profile user:inference user:sessions:claude_code "
    "user:mcp_servers user:file_upload"
)
ANTHROPIC_CALLBACK_PORT = 53692
ANTHROPIC_TOKEN_SKEW_MS = 5 * 60 * 1000


async def login_anthropic(
    *,
    on_auth: Callable[[OAuthAuthInfo], None],
    on_prompt: Callable[[OAuthPrompt], Awaitable[str]],
    on_manual_code_input: Callable[[], Awaitable[str]] | None = None,
    on_progress: Callable[[str], None] | None = None,
    open_browser: bool = True,
    client: httpx.AsyncClient | None = None,
) -> OAuthCredential:
    """Run Anthropic's authorization-code + PKCE login flow."""
    verifier, challenge = create_pkce_pair()
    params = {
        "code": "true",
        "client_id": ANTHROPIC_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": ANTHROPIC_REDIRECT_URI,
        "scope": ANTHROPIC_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    url = f"{ANTHROPIC_AUTHORIZE_URL}?{urlencode(params)}"
    server = await _start_local_oauth_server(
        verifier,
        callback_port=ANTHROPIC_CALLBACK_PORT,
        callback_path="/callback",
        success_message="Anthropic authentication completed. You can close this window.",
    )
    on_auth(
        OAuthAuthInfo(
            url=url,
            instructions=(
                "Complete login in your browser. If the browser is on another machine, "
                "paste the final redirect URL here."
            ),
        )
    )
    if open_browser:
        webbrowser.open(url)

    try:
        value = await _wait_for_input(server, on_manual_code_input)
        if value is None:
            value = await on_prompt(
                OAuthPrompt(
                    message="Paste the authorization code or full redirect URL:",
                    placeholder=ANTHROPIC_REDIRECT_URI,
                )
            )
        parsed = parse_authorization_input(value)
        if parsed.state is not None and parsed.state != verifier:
            raise OAuthError("OAuth state mismatch")
        if not parsed.code:
            raise OAuthError("Missing authorization code")
        on_progress and on_progress("Exchanging authorization code for tokens...")
        return await _anthropic_token_request(
            {
                "grant_type": "authorization_code",
                "client_id": ANTHROPIC_CLIENT_ID,
                "code": parsed.code,
                "state": parsed.state or verifier,
                "redirect_uri": ANTHROPIC_REDIRECT_URI,
                "code_verifier": verifier,
            },
            client=client,
            action="exchange",
        )
    finally:
        if server is not None:
            server.close()


async def refresh_anthropic_token(
    refresh_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> OAuthCredential:
    """Refresh Anthropic OAuth credentials."""
    return await _anthropic_token_request(
        {
            "grant_type": "refresh_token",
            "client_id": ANTHROPIC_CLIENT_ID,
            "refresh_token": refresh_token,
        },
        client=client,
        action="refresh",
        previous_refresh=refresh_token,
    )


async def _anthropic_token_request(
    data: dict[str, str],
    *,
    client: httpx.AsyncClient | None,
    action: str,
    previous_refresh: str | None = None,
) -> OAuthCredential:
    owns_client = client is None
    active_client = client or create_async_client(timeout=30)
    try:
        response = await active_client.post(
            ANTHROPIC_TOKEN_URL,
            json=data,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    finally:
        if owns_client:
            await active_client.aclose()
    if response.status_code >= 400:
        raise OAuthError(f"Anthropic token {action} failed ({response.status_code})")
    raw = response.json()
    if not isinstance(raw, dict):
        raise OAuthError(f"Anthropic token {action} response must be an object")
    access = _required_string(raw, "access_token", action=action)
    refresh = _optional_string(raw, "refresh_token") or previous_refresh
    expires_in = raw.get("expires_in")
    if refresh is None:
        raise OAuthError(f"Anthropic token {action} response missing refresh_token")
    if not isinstance(expires_in, int | float) or isinstance(expires_in, bool) or expires_in <= 0:
        raise OAuthError(f"Anthropic token {action} response missing expires_in")
    return OAuthCredential(
        access=access,
        refresh=refresh,
        expires=int(time.time() * 1000 + expires_in * 1000 - ANTHROPIC_TOKEN_SKEW_MS),
    )


async def _wait_for_input(
    server: Any,
    manual_callback: Callable[[], Awaitable[str]] | None,
) -> str | None:
    tasks: list[asyncio.Task[str | None]] = []
    if server is not None:
        tasks.append(asyncio.create_task(server.wait_for_code()))
    if manual_callback is not None:
        tasks.append(asyncio.create_task(_manual_value(manual_callback)))
    if not tasks:
        return None
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    if server is not None:
        server.cancel_wait()
    return next(iter(done)).result()


async def _manual_value(callback: Callable[[], Awaitable[str]]) -> str:
    return await callback()


def _required_string(raw: dict[str, Any], name: str, *, action: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value:
        raise OAuthError(f"Anthropic token {action} response missing {name}")
    return value


def _optional_string(raw: dict[str, Any], name: str) -> str | None:
    value = raw.get(name)
    return value if isinstance(value, str) and value else None


class AnthropicOAuthProvider:
    """Registered Anthropic Claude subscription OAuth behavior."""

    id = ANTHROPIC_OAUTH_PROVIDER
    name = "Anthropic (Claude Pro/Max)"
    flow_kinds: tuple[OAuthFlowKind, ...] = ("browser",)

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential:
        return await login_anthropic(
            on_auth=callbacks.on_auth,
            on_prompt=callbacks.on_prompt,
            on_manual_code_input=callbacks.on_manual_code_input,
            on_progress=callbacks.on_progress,
        )

    async def refresh(self, credential: OAuthCredential) -> OAuthCredential:
        if not oauth_credential_is_expired(credential):
            return credential
        return await refresh_anthropic_token(credential.refresh)

    def runtime_auth(self, credential: OAuthCredential) -> OAuthRuntimeAuth:
        return OAuthRuntimeAuth(
            api_key=credential.access,
            headers={
                "Authorization": f"Bearer {credential.access}",
                "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
                "user-agent": "claude-cli/tau",
                "x-app": "cli",
            },
        )
