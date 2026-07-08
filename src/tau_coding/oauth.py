"""OAuth helpers for subscription-backed coding providers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import threading
import time
import webbrowser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import dumps, loads
from os import environ
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from tau_ai.http import create_async_client
from tau_coding.credentials import OAuthCredential

OPENAI_CODEX_OAUTH_PROVIDER = "openai-codex"
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_CODEX_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENAI_CODEX_SCOPE = "openid profile email offline_access"
OPENAI_CODEX_ACCOUNT_CLAIM = "https://api.openai.com/auth"
OPENAI_CODEX_CALLBACK_PORT = 1455
TOKEN_REFRESH_SKEW_MS = 60_000

type AuthCallback = Callable[["OAuthAuthInfo"], None]
type PromptCallback = Callable[["OAuthPrompt"], Awaitable[str]]
type ManualCodeCallback = Callable[[], Awaitable[str]]
type ProgressCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class OAuthAuthInfo:
    """Authorization URL and optional user instructions."""

    url: str
    instructions: str | None = None


@dataclass(frozen=True, slots=True)
class OAuthPrompt:
    """Text prompt used for manual OAuth fallback input."""

    message: str
    placeholder: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationCode:
    """Parsed OAuth authorization callback data."""

    code: str | None = None
    state: str | None = None


@dataclass(frozen=True, slots=True)
class AuthorizationFlow:
    """OpenAI Codex OAuth authorization flow state."""

    verifier: str
    state: str
    url: str


@dataclass(frozen=True, slots=True)
class TokenResponse:
    """Successful OAuth token response."""

    access: str
    refresh: str
    expires: int


class OAuthError(RuntimeError):
    """Raised when an OAuth flow cannot complete."""


class _LocalOAuthServer:
    def __init__(
        self,
        server: ThreadingHTTPServer,
        thread: threading.Thread,
        future: asyncio.Future[str | None],
    ) -> None:
        self._server = server
        self._thread = thread
        self._future = future

    async def wait_for_code(self) -> str | None:
        """Wait for the local callback server to receive a code."""
        return await self._future

    def cancel_wait(self) -> None:
        """Resolve the pending wait without an authorization code."""
        if not self._future.done():
            self._future.set_result(None)

    def close(self) -> None:
        """Stop the local callback server."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)


def create_pkce_pair() -> tuple[str, str]:
    """Return a PKCE verifier and S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = _base64url(digest)
    return verifier, challenge


def create_openai_codex_authorization_flow(
    *,
    originator: str = "tau",
) -> AuthorizationFlow:
    """Create an OpenAI Codex OAuth authorization URL."""
    verifier, challenge = create_pkce_pair()
    state = secrets.token_hex(16)
    params = {
        "response_type": "code",
        "client_id": OPENAI_CODEX_CLIENT_ID,
        "redirect_uri": OPENAI_CODEX_REDIRECT_URI,
        "scope": OPENAI_CODEX_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": originator,
    }
    return AuthorizationFlow(
        verifier=verifier,
        state=state,
        url=f"{OPENAI_CODEX_AUTHORIZE_URL}?{urlencode(params)}",
    )


def parse_authorization_input(value: str) -> AuthorizationCode:
    """Parse a pasted redirect URL, query string, code#state pair, or raw code."""
    stripped = value.strip()
    if not stripped:
        return AuthorizationCode()

    parsed_url = urlparse(stripped)
    if parsed_url.scheme and parsed_url.netloc:
        params = parse_qs(parsed_url.query)
        return AuthorizationCode(
            code=_first_query_value(params, "code"),
            state=_first_query_value(params, "state"),
        )

    if "#" in stripped:
        code, state = stripped.split("#", 1)
        return AuthorizationCode(code=code or None, state=state or None)

    if "code=" in stripped:
        params = parse_qs(stripped)
        return AuthorizationCode(
            code=_first_query_value(params, "code"),
            state=_first_query_value(params, "state"),
        )

    return AuthorizationCode(code=stripped)


def oauth_credential_is_expired(credential: OAuthCredential) -> bool:
    """Return whether an OAuth credential should be refreshed before use."""
    return int(time.time() * 1000) >= credential.expires - TOKEN_REFRESH_SKEW_MS


async def login_openai_codex(
    *,
    on_auth: AuthCallback,
    on_prompt: PromptCallback,
    on_manual_code_input: ManualCodeCallback | None = None,
    on_progress: ProgressCallback | None = None,
    open_browser: bool = True,
    originator: str = "tau",
    client: httpx.AsyncClient | None = None,
) -> OAuthCredential:
    """Run OpenAI Codex OAuth and return refreshable credentials."""
    flow = create_openai_codex_authorization_flow(originator=originator)
    server = await _start_local_oauth_server(flow.state)

    on_auth(
        OAuthAuthInfo(
            url=flow.url,
            instructions="A browser window should open. Complete login to finish.",
        )
    )
    if open_browser:
        webbrowser.open(flow.url)

    try:
        code = await _wait_for_authorization_code(
            flow=flow,
            server=server,
            on_manual_code_input=on_manual_code_input,
        )
        if code is None:
            manual_input = await on_prompt(
                OAuthPrompt(message="Paste the authorization code or full redirect URL:")
            )
            parsed = parse_authorization_input(manual_input)
            _validate_state(parsed.state, flow.state)
            code = parsed.code
        if not code:
            raise OAuthError("Missing authorization code")
        on_progress and on_progress("Exchanging authorization code...")
        token = await exchange_openai_codex_authorization_code(
            code,
            flow.verifier,
            client=client,
        )
        account_id = account_id_from_access_token(token.access)
        if account_id is None:
            raise OAuthError("Failed to extract OpenAI account id from access token")
        return OAuthCredential(
            access=token.access,
            refresh=token.refresh,
            expires=token.expires,
            account_id=account_id,
        )
    finally:
        if server is not None:
            server.close()


async def exchange_openai_codex_authorization_code(
    code: str,
    verifier: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> TokenResponse:
    """Exchange an OpenAI Codex authorization code for OAuth tokens."""
    raw = await _post_openai_codex_token(
        {
            "grant_type": "authorization_code",
            "client_id": OPENAI_CODEX_CLIENT_ID,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": OPENAI_CODEX_REDIRECT_URI,
        },
        client=client,
        action="exchange",
    )
    access_token = _required_token_field(raw, "access_token", action="exchange")
    refresh_token = _required_token_field(raw, "refresh_token", action="exchange")
    return TokenResponse(
        access=access_token,
        refresh=refresh_token,
        expires=_token_expiry(raw, access_token, action="exchange"),
    )


async def refresh_openai_codex_token(
    refresh_token: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> OAuthCredential:
    """Refresh OpenAI Codex OAuth credentials."""
    raw = await _post_openai_codex_token(
        {
            "grant_type": "refresh_token",
            "client_id": OPENAI_CODEX_CLIENT_ID,
            "refresh_token": refresh_token,
        },
        client=client,
        action="refresh",
    )
    access_token = _required_token_field(raw, "access_token", action="refresh")
    next_refresh_token = _optional_token_field(raw, "refresh_token") or refresh_token
    account_id = account_id_from_access_token(access_token)
    if account_id is None:
        raise OAuthError("Failed to extract OpenAI account id from refreshed access token")
    return OAuthCredential(
        access=access_token,
        refresh=next_refresh_token,
        expires=_token_expiry(raw, access_token, action="refresh"),
        account_id=account_id,
    )


def account_id_from_access_token(access_token: str) -> str | None:
    """Extract the ChatGPT account id from an OpenAI Codex access JWT."""
    payload = _access_token_payload(access_token)
    if payload is None:
        return None
    auth = payload.get(OPENAI_CODEX_ACCOUNT_CLAIM)
    if not isinstance(auth, dict):
        return None
    account_id = auth.get("chatgpt_account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        return None
    return account_id.strip()


def _access_token_expiry(access_token: str) -> int | None:
    payload = _access_token_payload(access_token)
    if payload is None:
        return None
    exp = payload.get("exp")
    if isinstance(exp, int | float) and not isinstance(exp, bool) and exp > 0:
        return int(exp * 1000)
    return None


def _access_token_payload(access_token: str) -> dict[str, Any] | None:
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            return None
        payload = loads(_base64url_decode(parts[1]).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


async def _post_openai_codex_token(
    data: dict[str, str],
    *,
    client: httpx.AsyncClient | None,
    action: str,
) -> dict[str, Any]:
    owns_client = client is None
    active_client = client or create_async_client(timeout=60)
    try:
        response = await active_client.post(
            OPENAI_CODEX_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    finally:
        if owns_client:
            await active_client.aclose()

    if response.status_code >= 400:
        raise OAuthError(
            f"OpenAI Codex token {action} failed ({response.status_code}): {response.text}"
        )

    raw = response.json()
    if not isinstance(raw, dict):
        raise OAuthError(f"OpenAI Codex token {action} response must be a JSON object")
    return raw


def _required_token_field(raw: dict[str, Any], field: str, *, action: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise OAuthError(
            f"OpenAI Codex token {action} response missing {field}: {dumps(raw, sort_keys=True)}"
        )
    return value


def _optional_token_field(raw: dict[str, Any], field: str) -> str | None:
    value = raw.get(field)
    if isinstance(value, str) and value:
        return value
    return None


def _token_expiry(raw: dict[str, Any], access_token: str, *, action: str) -> int:
    expires_in = raw.get("expires_in")
    if isinstance(expires_in, int | float) and not isinstance(expires_in, bool):
        return int(time.time() * 1000) + int(expires_in * 1000)
    if expires_in is not None:
        raise OAuthError(
            f"OpenAI Codex token {action} response has invalid expires_in: "
            f"{dumps(raw, sort_keys=True)}"
        )
    expires = _access_token_expiry(access_token)
    if expires is not None:
        return expires
    raise OAuthError(
        f"OpenAI Codex token {action} response missing expiry: {dumps(raw, sort_keys=True)}"
    )


async def _wait_for_authorization_code(
    *,
    flow: AuthorizationFlow,
    server: _LocalOAuthServer | None,
    on_manual_code_input: ManualCodeCallback | None,
) -> str | None:
    tasks: list[asyncio.Task[str | None]] = []
    if server is not None:
        tasks.append(asyncio.create_task(server.wait_for_code()))
    if on_manual_code_input is not None:
        tasks.append(asyncio.create_task(_await_manual_code(on_manual_code_input)))
    if not tasks:
        return None

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        result = next(iter(done)).result()
    finally:
        if server is not None:
            server.cancel_wait()

    if result is None:
        return None
    parsed = parse_authorization_input(result)
    _validate_state(parsed.state, flow.state)
    return parsed.code


async def _await_manual_code(callback: ManualCodeCallback) -> str | None:
    return await callback()


def _validate_state(state: str | None, expected_state: str) -> None:
    if state is not None and state != expected_state:
        raise OAuthError("OAuth state mismatch")


async def _start_local_oauth_server(state: str) -> _LocalOAuthServer | None:
    host = environ.get("TAU_OAUTH_CALLBACK_HOST", "127.0.0.1")
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str | None] = loop.create_future()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            try:
                parsed = urlparse(self.path)
                if parsed.path != "/auth/callback":
                    self._finish(404, _oauth_html("Callback route not found."))
                    return
                params = parse_qs(parsed.query)
                if _first_query_value(params, "state") != state:
                    self._finish(400, _oauth_html("OAuth state mismatch."))
                    return
                code = _first_query_value(params, "code")
                if not code:
                    self._finish(400, _oauth_html("Missing authorization code."))
                    return
                self._finish(
                    200,
                    _oauth_html("OpenAI authentication completed. You can close this window."),
                )
                if not future.done():
                    loop.call_soon_threadsafe(future.set_result, code)
            except Exception:
                self._finish(500, _oauth_html("Internal error while processing OAuth callback."))

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _finish(self, status: int, body: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    try:
        server = ThreadingHTTPServer((host, OPENAI_CODEX_CALLBACK_PORT), CallbackHandler)
    except OSError:
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return _LocalOAuthServer(server, thread, future)


def _first_query_value(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    if not values:
        return None
    return values[0] or None


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _oauth_html(message: str) -> str:
    escaped = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    return f'<!doctype html><meta charset="utf-8"><title>Tau OAuth</title><p>{escaped}</p>'
