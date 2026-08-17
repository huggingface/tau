import asyncio
from typing import cast

import httpx
import pytest

from tau_coding.credentials import FileCredentialStore, OAuthCredential
from tau_coding.oauth import OAuthError, oauth_credential_is_expired
from tau_coding.oauth_anthropic import (
    ANTHROPIC_CLIENT_ID,
    ANTHROPIC_TOKEN_URL,
    refresh_anthropic_token,
)
from tau_coding.oauth_device import DevicePollResult, poll_oauth_device_code
from tau_coding.oauth_github_copilot import (
    GITHUB_COPILOT_CLIENT_ID,
    github_copilot_base_url,
    login_github_copilot,
    normalize_github_domain,
    refresh_github_copilot_token,
)
from tau_coding.oauth_registry import (
    get_oauth_provider,
    oauth_provider_ids,
    register_oauth_provider,
    reset_oauth_providers,
    unregister_oauth_provider,
)
from tau_coding.oauth_types import (
    OAuthDeviceCodeInfo,
    OAuthLoginCallbacks,
    OAuthPrompt,
    OAuthProvider,
    OAuthRuntimeAuth,
    OAuthSelectPrompt,
)
from tau_coding.provider_config import provider_config_from_catalog_entry
from tau_coding.provider_runtime import OAuthRuntimeCredentialResolver, _refresh_lock


def _callbacks(
    *,
    prompt: str = "",
    device_codes: list[OAuthDeviceCodeInfo] | None = None,
) -> OAuthLoginCallbacks:
    async def on_prompt(_prompt: OAuthPrompt) -> str:
        return prompt

    async def on_select(_prompt: OAuthSelectPrompt) -> str | None:
        return None

    return OAuthLoginCallbacks(
        on_auth=lambda _info: None,
        on_device_code=lambda info: device_codes.append(info) if device_codes is not None else None,
        on_prompt=on_prompt,
        on_select=on_select,
    )


@pytest.mark.anyio
async def test_refresh_anthropic_token_uses_json_and_redacts_failed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == ANTHROPIC_TOKEN_URL
        assert request.headers["content-type"] == "application/json"
        assert request.content
        assert ANTHROPIC_CLIENT_ID.encode() in request.content
        return httpx.Response(401, text="secret-token-body")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OAuthError) as error:
            await refresh_anthropic_token("refresh-secret", client=client)

    assert "401" in str(error.value)
    assert "secret-token-body" not in str(error.value)
    assert "refresh-secret" not in str(error.value)


@pytest.mark.anyio
async def test_refresh_anthropic_token_reports_structured_oauth_error() -> None:
    """A dead refresh token should say so, not just report a status code."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": "invalid_grant",
                "error_description": "Refresh token not found or invalid",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OAuthError) as error:
            await refresh_anthropic_token("refresh-secret", client=client)

    assert "invalid_grant: Refresh token not found or invalid" in str(error.value)
    assert "refresh-secret" not in str(error.value)


@pytest.mark.anyio
async def test_refresh_anthropic_token_reports_nested_error_without_echoing_token() -> None:
    """Anthropic's nested envelope still yields detail, minus anything we sent."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "refresh_token refresh-secret is malformed. " + "x" * 400,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OAuthError) as error:
            await refresh_anthropic_token("refresh-secret", client=client)

    message = str(error.value)
    assert "invalid_request_error: refresh_token <redacted> is malformed." in message
    assert "refresh-secret" not in message
    assert len(message) < 300


@pytest.mark.anyio
async def test_refresh_anthropic_token_scrubs_a_token_before_truncating() -> None:
    """Scrub then truncate: the other order leaks the surviving prefix."""
    secret = "refresh-" + "s" * 40

    def handler(_request: httpx.Request) -> httpx.Response:
        # Place the token so it straddles the 200-character truncation point.
        return httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "y" * 175 + secret},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OAuthError) as error:
            await refresh_anthropic_token(secret, client=client)

    message = str(error.value)
    # Truncating first would have left the token's leading characters here.
    assert message.endswith("<redacted>")
    assert "refresh-ss" not in message


@pytest.mark.anyio
async def test_refresh_anthropic_token_returns_provider_neutral_credential() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": "anthropic-access",
                "refresh_token": "anthropic-refresh",
                "expires_in": 3600,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        credential = await refresh_anthropic_token("old-refresh", client=client)

    assert credential.access == "anthropic-access"
    assert credential.refresh == "anthropic-refresh"
    assert credential.account_id is None
    assert credential.expires > 0


@pytest.mark.anyio
async def test_github_copilot_device_login_and_token_exchange() -> None:
    device_codes: list[OAuthDeviceCodeInfo] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/device/code":
            assert f"client_id={GITHUB_COPILOT_CLIENT_ID}" in request.content.decode()
            return httpx.Response(
                200,
                json={
                    "device_code": "device-secret",
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 0,
                    "expires_in": 60,
                },
            )
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "github-token"})
        if request.url.path == "/copilot_internal/v2/token":
            assert request.headers["authorization"] == "Bearer github-token"
            return httpx.Response(
                200,
                json={
                    "token": "tid=1;exp=9999999999;proxy-ep=proxy.business.githubcopilot.com",
                    "expires_at": 9999999999,
                },
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        credential = await login_github_copilot(
            _callbacks(device_codes=device_codes),
            client=client,
        )

    assert device_codes == [
        OAuthDeviceCodeInfo(
            user_code="ABCD-1234",
            verification_uri="https://github.com/login/device",
            interval_seconds=0,
            expires_in_seconds=60,
        )
    ]
    assert credential.refresh == "github-token"
    assert credential.access.startswith("tid=1")
    assert github_copilot_base_url(credential.access) == ("https://api.business.githubcopilot.com")


@pytest.mark.anyio
async def test_github_copilot_rejects_untrusted_device_verification_uri() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "device_code": "device",
                "user_code": "code",
                "verification_uri": "file:///tmp/not-safe",
                "interval": 5,
                "expires_in": 60,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OAuthError, match="Untrusted verification_uri"):
            await login_github_copilot(_callbacks(), client=client)


@pytest.mark.anyio
async def test_refresh_github_copilot_preserves_enterprise_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.ghe.example.com"
        return httpx.Response(200, json={"token": "copilot", "expires_at": 9999999999})

    original = OAuthCredential(
        access="old",
        refresh="github-token",
        expires=1,
        metadata={"enterprise_domain": "ghe.example.com"},
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        refreshed = await refresh_github_copilot_token(original, client=client)

    assert refreshed.metadata == original.metadata
    assert normalize_github_domain("https://ghe.example.com/path") == "ghe.example.com"
    assert github_copilot_base_url(None, "ghe.example.com") == (
        "https://copilot-api.ghe.example.com"
    )


@pytest.mark.anyio
async def test_device_poll_slow_down_and_cancel() -> None:
    sleeps: list[float] = []
    results = iter(
        [
            DevicePollResult[str](status="slow_down"),
            DevicePollResult(status="complete", value="done"),
        ]
    )

    async def fake_poll() -> DevicePollResult[str]:
        return next(results)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    assert (
        await poll_oauth_device_code(
            fake_poll,
            interval_seconds=1,
            expires_in_seconds=60,
            sleep=fake_sleep,
        )
        == "done"
    )
    assert sleeps == [6]

    cancel_event = asyncio.Event()
    cancel_event.set()
    with pytest.raises(OAuthError, match="Login cancelled"):
        await poll_oauth_device_code(fake_poll, cancel_event=cancel_event)


@pytest.mark.anyio
async def test_runtime_oauth_resolver_refreshes_and_persists_atomically(tmp_path) -> None:
    class FakeOAuthProvider:
        id = "github-copilot"
        name = "Fake GitHub Copilot"
        flow_kinds = ("device_code",)

        async def login(self, _callbacks: OAuthLoginCallbacks) -> OAuthCredential:
            raise AssertionError("not used")

        async def refresh(self, credential: OAuthCredential) -> OAuthCredential:
            return OAuthCredential(
                access="new-access",
                refresh=credential.refresh,
                expires=9999999999999,
                metadata=dict(credential.metadata),
            )

        def runtime_auth(self, credential: OAuthCredential) -> OAuthRuntimeAuth:
            return OAuthRuntimeAuth(
                api_key=credential.access,
                base_url="https://api.business.githubcopilot.com",
            )

    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_oauth(
        "github-copilot",
        OAuthCredential(access="old", refresh="github", expires=1),
    )
    provider = provider_config_from_catalog_entry("github-copilot")
    fake = cast(OAuthProvider, FakeOAuthProvider())
    register_oauth_provider(fake)
    try:
        auth = await OAuthRuntimeCredentialResolver(provider, credential_store=store)()
    finally:
        unregister_oauth_provider("github-copilot")
        reset_oauth_providers()

    assert auth.api_key == "new-access"
    assert auth.base_url == "https://api.business.githubcopilot.com"
    saved = store.get_oauth("github-copilot")
    assert saved is not None
    assert saved.access == "new-access"
    assert not list(tmp_path.glob(".credentials.json.*"))


@pytest.mark.anyio
async def test_runtime_oauth_resolver_spends_a_refresh_token_once(tmp_path) -> None:
    """Concurrent calls must not both spend the same rotating refresh token."""
    refreshes: list[str] = []

    class RotatingOAuthProvider:
        id = "github-copilot"
        name = "Rotating"
        flow_kinds = ("device_code",)

        async def login(self, _callbacks: OAuthLoginCallbacks) -> OAuthCredential:
            raise AssertionError("not used")

        async def refresh(self, credential: OAuthCredential) -> OAuthCredential:
            if not oauth_credential_is_expired(credential):
                return credential
            if credential.refresh in refreshes:
                raise OAuthError("invalid_grant: Refresh token not found or invalid")
            refreshes.append(credential.refresh)
            await asyncio.sleep(0)  # let a racing task reach the same refresh
            return OAuthCredential(
                access="access-2",
                refresh="refresh-2",
                expires=9999999999999,
            )

        def runtime_auth(self, credential: OAuthCredential) -> OAuthRuntimeAuth:
            return OAuthRuntimeAuth(api_key=credential.access)

    store = FileCredentialStore(tmp_path / "credentials.json")
    store.set_oauth(
        "github-copilot",
        OAuthCredential(access="access-1", refresh="refresh-1", expires=1),
    )
    provider = provider_config_from_catalog_entry("github-copilot")
    resolver = OAuthRuntimeCredentialResolver(provider, credential_store=store)
    register_oauth_provider(cast(OAuthProvider, RotatingOAuthProvider()))
    try:
        results = await asyncio.gather(resolver(), resolver(), resolver())
    finally:
        unregister_oauth_provider("github-copilot")
        reset_oauth_providers()

    assert refreshes == ["refresh-1"]
    assert [auth.api_key for auth in results] == ["access-2"] * 3
    saved = store.get_oauth("github-copilot")
    assert saved is not None
    # The rotated token is what survives on disk, so the next run can refresh.
    assert saved.refresh == "refresh-2"


def test_refresh_locks_are_not_shared_between_event_loops() -> None:
    """A lock cached across loops only fails once contended — so contend it."""

    async def _hold(lock: asyncio.Lock) -> None:
        async with lock:
            await asyncio.sleep(0)

    async def contend() -> asyncio.Lock:
        lock = _refresh_lock("anthropic")
        async with asyncio.timeout(5):
            await asyncio.gather(_hold(lock), _hold(lock))
        return lock

    # The assertion that matters is that neither run raised "bound to a
    # different event loop" — a lock reused across loops dies on the second
    # contention. The identity check below cannot fail on its own (distinct
    # loops are distinct keys); it is here to say what the fix is supposed to
    # produce, not to detect the bug.
    first = asyncio.run(contend())
    second = asyncio.run(contend())

    assert first is not second


def test_builtin_oauth_registry_matches_supported_subscription_providers() -> None:
    assert oauth_provider_ids() == {"anthropic", "github-copilot", "openai-codex"}
    anthropic = get_oauth_provider("anthropic")
    assert anthropic is not None
    assert anthropic.name == "Anthropic (Claude Pro/Max)"
    assert get_oauth_provider("missing") is None
