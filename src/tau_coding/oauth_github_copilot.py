"""GitHub Copilot OAuth device-code provider."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
    OAuthPrompt,
    OAuthRuntimeAuth,
    oauth_metadata_string,
)

GITHUB_COPILOT_OAUTH_PROVIDER = "github-copilot"
GITHUB_COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
GITHUB_COPILOT_API_VERSION = "2026-06-01"
GITHUB_COPILOT_TOKEN_SKEW_MS = 5 * 60 * 1000
GITHUB_COPILOT_HEADERS = {
    "User-Agent": "GitHubCopilotChat/0.35.0",
    "Editor-Version": "vscode/1.107.0",
    "Editor-Plugin-Version": "copilot-chat/0.35.0",
    "Copilot-Integration-Id": "vscode-chat",
}


@dataclass(frozen=True, slots=True)
class GitHubDeviceCode:
    """Validated GitHub device authorization response."""

    device_code: str
    user_code: str
    verification_uri: str
    interval_seconds: float
    expires_in_seconds: float


def normalize_github_domain(value: str) -> str | None:
    """Normalize a GitHub Enterprise URL/domain to a hostname."""
    stripped = value.strip()
    if not stripped:
        return None
    parsed = urlparse(stripped if "://" in stripped else f"https://{stripped}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed.hostname


def github_copilot_base_url(token: str | None, enterprise_domain: str | None = None) -> str:
    """Derive the Copilot API URL encoded in a short-lived Copilot token."""
    if token:
        for field in token.split(";"):
            key, separator, value = field.partition("=")
            if separator and key == "proxy-ep" and value:
                api_host = f"api.{value.removeprefix('proxy.')}"
                return f"https://{api_host}"
    if enterprise_domain:
        return f"https://copilot-api.{enterprise_domain}"
    return "https://api.individual.githubcopilot.com"


async def login_github_copilot(
    callbacks: OAuthLoginCallbacks,
    *,
    client: httpx.AsyncClient | None = None,
    cancel_event: asyncio.Event | None = None,
) -> OAuthCredential:
    """Run GitHub's device flow and exchange its token for Copilot auth."""
    domain_input = await callbacks.on_prompt(
        OAuthPrompt(
            message="GitHub Enterprise URL/domain (blank for github.com)",
            placeholder="company.ghe.com",
            allow_empty=True,
        )
    )
    if cancel_event is not None and cancel_event.is_set():
        raise OAuthError("Login cancelled")
    enterprise_domain = normalize_github_domain(domain_input)
    if domain_input.strip() and enterprise_domain is None:
        raise OAuthError("Invalid GitHub Enterprise URL/domain")
    domain = enterprise_domain or "github.com"

    owns_client = client is None
    active_client = client or create_async_client(timeout=30)
    try:
        device = await _start_device_flow(domain, active_client)
        callbacks.on_device_code(
            OAuthDeviceCodeInfo(
                user_code=device.user_code,
                verification_uri=device.verification_uri,
                interval_seconds=device.interval_seconds,
                expires_in_seconds=device.expires_in_seconds,
            )
        )
        github_token = await _poll_github_access_token(
            domain,
            device,
            active_client,
            cancel_event=cancel_event,
        )
        if callbacks.on_progress:
            callbacks.on_progress("Exchanging GitHub token for Copilot access...")
        return await refresh_github_copilot_token(
            OAuthCredential(
                access=github_token,
                refresh=github_token,
                expires=1,
                metadata=({"enterprise_domain": enterprise_domain} if enterprise_domain else {}),
            ),
            client=active_client,
        )
    finally:
        if owns_client:
            await active_client.aclose()


async def refresh_github_copilot_token(
    credential: OAuthCredential,
    *,
    client: httpx.AsyncClient | None = None,
) -> OAuthCredential:
    """Exchange a long-lived GitHub token for a short-lived Copilot token."""
    enterprise_domain = oauth_metadata_string(credential.metadata, "enterprise_domain")
    domain = enterprise_domain or "github.com"
    owns_client = client is None
    active_client = client or create_async_client(timeout=30)
    try:
        response = await active_client.get(
            f"https://api.{domain}/copilot_internal/v2/token",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {credential.refresh}",
                **GITHUB_COPILOT_HEADERS,
            },
        )
    finally:
        if owns_client:
            await active_client.aclose()
    raw = _response_object(response, "Copilot token")
    token = _required_string(raw, "token", "Copilot token")
    expires_at = raw.get("expires_at")
    if not isinstance(expires_at, int | float) or isinstance(expires_at, bool):
        raise OAuthError("Copilot token response missing expires_at")
    return OAuthCredential(
        access=token,
        refresh=credential.refresh,
        expires=int(expires_at * 1000 - GITHUB_COPILOT_TOKEN_SKEW_MS),
        account_id=credential.account_id,
        metadata=dict(credential.metadata),
    )


async def _start_device_flow(domain: str, client: httpx.AsyncClient) -> GitHubDeviceCode:
    response = await client.post(
        f"https://{domain}/login/device/code",
        data={"client_id": GITHUB_COPILOT_CLIENT_ID, "scope": "read:user"},
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": GITHUB_COPILOT_HEADERS["User-Agent"],
        },
    )
    raw = _response_object(response, "GitHub device code")
    uri = _required_string(raw, "verification_uri", "GitHub device code")
    parsed_uri = urlparse(uri)
    if parsed_uri.scheme not in {"http", "https"} or not parsed_uri.netloc:
        raise OAuthError("Untrusted verification_uri in device code response")
    interval = raw.get("interval", 5)
    expires_in = raw.get("expires_in")
    if not isinstance(interval, int | float) or isinstance(interval, bool):
        raise OAuthError("GitHub device code response has invalid interval")
    if not isinstance(expires_in, int | float) or isinstance(expires_in, bool):
        raise OAuthError("GitHub device code response missing expires_in")
    return GitHubDeviceCode(
        device_code=_required_string(raw, "device_code", "GitHub device code"),
        user_code=_required_string(raw, "user_code", "GitHub device code"),
        verification_uri=uri,
        interval_seconds=float(interval),
        expires_in_seconds=float(expires_in),
    )


async def _poll_github_access_token(
    domain: str,
    device: GitHubDeviceCode,
    client: httpx.AsyncClient,
    *,
    cancel_event: asyncio.Event | None,
) -> str:
    async def poll() -> DevicePollResult[str]:
        response = await client.post(
            f"https://{domain}/login/oauth/access_token",
            data={
                "client_id": GITHUB_COPILOT_CLIENT_ID,
                "device_code": device.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": GITHUB_COPILOT_HEADERS["User-Agent"],
            },
        )
        raw = _response_object(response, "GitHub device token", accept_oauth_error=True)
        access_token = raw.get("access_token")
        if isinstance(access_token, str) and access_token:
            return DevicePollResult(status="complete", value=access_token)
        error = raw.get("error")
        if error == "authorization_pending":
            return DevicePollResult(status="pending")
        if error == "slow_down":
            interval = raw.get("interval")
            return DevicePollResult(
                status="slow_down",
                interval_seconds=float(interval) if isinstance(interval, int | float) else None,
            )
        description = raw.get("error_description")
        suffix = f": {description}" if isinstance(description, str) and description else ""
        return DevicePollResult(status="failed", message=f"Device flow failed: {error}{suffix}")

    return await poll_oauth_device_code(
        poll,
        interval_seconds=device.interval_seconds,
        expires_in_seconds=device.expires_in_seconds,
        wait_before_first_poll=True,
        cancel_event=cancel_event,
    )


def _response_object(
    response: httpx.Response,
    label: str,
    *,
    accept_oauth_error: bool = False,
) -> dict[str, Any]:
    if response.status_code >= 400 and not accept_oauth_error:
        raise OAuthError(f"{label} request failed ({response.status_code})")
    try:
        raw = response.json()
    except ValueError as exc:
        raise OAuthError(f"{label} response was not valid JSON") from exc
    if not isinstance(raw, dict):
        raise OAuthError(f"{label} response must be an object")
    return raw


def _required_string(raw: dict[str, Any], name: str, label: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value:
        raise OAuthError(f"{label} response missing {name}")
    return value


class GitHubCopilotOAuthProvider:
    """Registered GitHub Copilot OAuth behavior."""

    id = GITHUB_COPILOT_OAUTH_PROVIDER
    name = "GitHub Copilot"
    flow_kinds: tuple[OAuthFlowKind, ...] = ("device_code",)

    async def login(self, callbacks: OAuthLoginCallbacks) -> OAuthCredential:
        return await login_github_copilot(callbacks)

    async def refresh(self, credential: OAuthCredential) -> OAuthCredential:
        if not oauth_credential_is_expired(credential):
            return credential
        return await refresh_github_copilot_token(credential)

    def runtime_auth(self, credential: OAuthCredential) -> OAuthRuntimeAuth:
        enterprise_domain = oauth_metadata_string(credential.metadata, "enterprise_domain")
        return OAuthRuntimeAuth(
            api_key=credential.access,
            base_url=github_copilot_base_url(credential.access, enterprise_domain),
            headers=GITHUB_COPILOT_HEADERS,
        )
