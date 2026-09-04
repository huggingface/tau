"""xAI SuperGrok / X Premium OAuth device-code provider."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any
from urllib.parse import urlparse

import httpx

from tau_ai.http import create_async_client
from tau_coding.credentials import OAuthCredential
from tau_coding.oauth import OAuthError, oauth_credential_is_expired
from tau_coding.oauth_device import DevicePollResult, poll_oauth_device_code
from tau_coding.oauth_types import (
    OAuthDeviceCodeInfo,
    OAuthFlowKind,
    OAuthLoginCallbacks,
    OAuthRuntimeAuth,
)

XAI_OAUTH_PROVIDER = "xai"
# Public xAI device-code client currently accepted by auth.x.ai for CLI login.
# Device-code requests identify Tau with referrer=tau. Replace this ID if xAI
# issues a Tau-owned client.
XAI_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_SCOPE = "openid profile email offline_access grok-cli:access api:access"
XAI_DEVICE_CODE_URL = "https://auth.x.ai/oauth2/device/code"
XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"
XAI_REFERRER = "tau"
XAI_TOKEN_SKEW_MS = 5 * 60 * 1000
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600
HTTP_TIMEOUT_SECONDS = 30.0


async def login_xai(
    callbacks: OAuthLoginCallbacks,
    *,
    client: httpx.AsyncClient | None = None,
    cancel_event: asyncio.Event | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> OAuthCredential:
    """Run xAI's device-code flow and return credentials to persist."""
    owns_client = client is None
    active_client = client or create_async_client(timeout=HTTP_TIMEOUT_SECONDS)
    try:
        info, device_code = await _request_device_code(active_client)
        callbacks.on_device_code(info)
        return await poll_oauth_device_code(
            lambda: _poll_for_tokens(device_code, active_client),
            interval_seconds=info.interval_seconds,
            expires_in_seconds=info.expires_in_seconds,
            wait_before_first_poll=True,
            cancel_event=cancel_event,
            sleep=sleep if sleep is not None else asyncio.sleep,
        )
    finally:
        if owns_client:
            await active_client.aclose()


async def refresh_xai_token(
    credential: OAuthCredential,
    *,
    client: httpx.AsyncClient | None = None,
) -> OAuthCredential:
    """Exchange a refresh token for a new xAI access token."""
    owns_client = client is None
    active_client = client or create_async_client(timeout=HTTP_TIMEOUT_SECONDS)
    try:
        ok, status, body = await _post_form(
            XAI_TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "client_id": XAI_CLIENT_ID,
                "refresh_token": credential.refresh,
            },
            client=active_client,
        )
    finally:
        if owns_client:
            await active_client.aclose()
    if not ok:
        raise _request_failure(
            "token refresh",
            status,
            body,
            secrets=[credential.refresh],
        )
    # xAI may omit refresh_token on refresh; keep the previous one then.
    return _credential_from_token_response(
        body,
        previous_refresh_token=credential.refresh,
    )


async def _post_form(
    url: str,
    fields: dict[str, str],
    *,
    client: httpx.AsyncClient,
) -> tuple[bool, int, dict[str, Any]]:
    """POST one urlencoded OAuth request and return (ok, status, json body)."""
    try:
        response = await client.post(
            url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=fields,
        )
    except httpx.HTTPError as exc:
        raise OAuthError(f"xAI OAuth request failed: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        raise OAuthError(f"xAI OAuth returned invalid JSON (HTTP {response.status_code})") from None
    if not isinstance(body, dict):
        raise OAuthError(f"xAI OAuth returned invalid JSON (HTTP {response.status_code})")
    return response.is_success, response.status_code, body


def _required_string(body: dict[str, Any], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value:
        raise OAuthError(f"Invalid xAI OAuth response field: {field}")
    return value


def _positive_number(body: dict[str, Any], field: str) -> int:
    value = body.get(field)
    if not isinstance(value, int | float) or isinstance(value, bool) or not value > 0:
        raise OAuthError(f"Invalid xAI OAuth response field: {field}")
    return int(value)


def _optional_interval(body: dict[str, Any]) -> float | None:
    if body.get("interval") is None:
        return None
    return float(_positive_number(body, "interval"))


def _https_url(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise OAuthError("Untrusted verification URI in xAI OAuth response")
    return raw


def _error_detail(body: dict[str, Any], *, secrets: Iterable[str] = ()) -> str | None:
    """Return structured OAuth error text with request secrets scrubbed."""
    error = body.get("error") if isinstance(body.get("error"), str) else None
    description = (
        body.get("error_description") if isinstance(body.get("error_description"), str) else None
    )
    detail = ": ".join(part for part in (error, description) if part)
    if not detail:
        return None
    for secret in secrets:
        if len(secret) >= 8:
            detail = detail.replace(secret, "<redacted>")
    return detail[:200]


def _request_failure(
    action: str,
    status: int,
    body: dict[str, Any],
    *,
    secrets: Iterable[str] = (),
) -> OAuthError:
    detail = _error_detail(body, secrets=secrets)
    suffix = f": {detail}" if detail else ""
    return OAuthError(f"xAI OAuth {action} failed (HTTP {status}){suffix}")


def _credential_from_token_response(
    body: dict[str, Any],
    previous_refresh_token: str | None,
) -> OAuthCredential:
    access = _required_string(body, "access_token")
    raw_refresh = body.get("refresh_token")
    if raw_refresh is None:
        if previous_refresh_token is None:
            raise OAuthError("Invalid xAI OAuth response field: refresh_token")
        refresh = previous_refresh_token
    else:
        refresh = _required_string(body, "refresh_token")
    expires_in = body.get("expires_in")
    expires_in_seconds = (
        _positive_number(body, "expires_in")
        if expires_in is not None
        else DEFAULT_TOKEN_LIFETIME_SECONDS
    )
    return OAuthCredential(
        access=access,
        refresh=refresh,
        expires=int(time.time() * 1000) + expires_in_seconds * 1000 - XAI_TOKEN_SKEW_MS,
    )


async def _request_device_code(client: httpx.AsyncClient) -> tuple[OAuthDeviceCodeInfo, str]:
    ok, status, body = await _post_form(
        XAI_DEVICE_CODE_URL,
        {"client_id": XAI_CLIENT_ID, "scope": XAI_SCOPE, "referrer": XAI_REFERRER},
        client=client,
    )
    if not ok:
        raise _request_failure("device authorization", status, body)
    info = OAuthDeviceCodeInfo(
        user_code=_required_string(body, "user_code"),
        verification_uri=_https_url(_required_string(body, "verification_uri")),
        interval_seconds=_optional_interval(body),
        expires_in_seconds=float(_positive_number(body, "expires_in")),
    )
    complete = body.get("verification_uri_complete")
    if isinstance(complete, str) and complete:
        info = OAuthDeviceCodeInfo(
            user_code=info.user_code,
            verification_uri=_https_url(complete),
            interval_seconds=info.interval_seconds,
            expires_in_seconds=info.expires_in_seconds,
        )
    return info, _required_string(body, "device_code")


async def _poll_for_tokens(
    device_code: str,
    client: httpx.AsyncClient,
) -> DevicePollResult[OAuthCredential]:
    ok, status, body = await _post_form(
        XAI_TOKEN_URL,
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": XAI_CLIENT_ID,
            "device_code": device_code,
        },
        client=client,
    )
    if ok:
        return DevicePollResult(
            status="complete",
            value=_credential_from_token_response(body, previous_refresh_token=None),
        )
    error = body.get("error")
    if error == "authorization_pending":
        return DevicePollResult(status="pending")
    if error == "slow_down":
        interval = body.get("interval")
        return DevicePollResult(
            status="slow_down",
            interval_seconds=float(interval) if isinstance(interval, int | float) else None,
        )
    if error in ("access_denied", "authorization_denied"):
        return DevicePollResult(status="failed", message="xAI device authorization was denied")
    if error == "expired_token":
        return DevicePollResult(status="failed", message="xAI device code expired")
    return DevicePollResult(
        status="failed",
        message=_request_failure(
            "device token polling",
            status,
            body,
            secrets=[device_code],
        ).args[0],
    )


class XaiOAuthProvider:
    """xAI Grok subscription OAuth via the auth.x.ai device-code flow."""

    id = XAI_OAUTH_PROVIDER
    name = "xAI (SuperGrok/X Premium)"
    flow_kinds: tuple[OAuthFlowKind, ...] = ("device_code",)

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential:
        return await login_xai(callbacks)

    async def refresh(self, credential: OAuthCredential) -> OAuthCredential:
        if not oauth_credential_is_expired(credential):
            return credential
        return await refresh_xai_token(credential)

    def runtime_auth(self, credential: OAuthCredential) -> OAuthRuntimeAuth:
        return OAuthRuntimeAuth(api_key=credential.access)
