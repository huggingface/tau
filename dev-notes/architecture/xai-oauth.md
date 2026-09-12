# xAI SuperGrok / X Premium OAuth

Issue: [#676](https://github.com/huggingface/tau/issues/676)

## What this adds

xAI was already a built-in OpenAI-compatible catalog provider, but login was
API-key only. This change registers a Tau-owned device-code OAuth provider so
SuperGrok / X Premium users can run `/login xai` without a paid API key.

The existing `XAI_API_KEY` path stays available:

```text
auth_methods = ["api_key", "oauth"]
```

This is a provider-specific follow-up to the registry from
[OAuth provider parity](./oauth-provider-parity.md). It does not change
`tau_agent` or add a new auth architecture.

## Architecture

```text
Textual OAuthLoginScreen
        │ OAuthLoginCallbacks (device code, progress, cancellation)
        ▼
tau_coding.oauth_registry
        │ OAuthProvider protocol
        └── XaiOAuthProvider  (oauth_xai.py)
        │
        ▼
FileCredentialStore ── OAuthRuntimeCredentialResolver ── OpenAI-compatible adapter
```

`tau_coding` owns the device-code flow, refresh, credential storage, and
`/login` policy. The existing `api.x.ai` OpenAI-compatible adapter in `tau_ai`
receives only a Bearer access token. `tau_agent` is unchanged.

Device-code requests identify Tau as the client with `referrer=tau`. Login,
refresh, and runtime auth follow the same `OAuthProvider` contract as Anthropic,
Codex, and Copilot.

## Behavior

- `/login` → Subscription lists xAI (SuperGrok / X Premium).
- `/login xai` and `/login xai-subscription` start RFC 8628 device authorization
  against `auth.x.ai`: show the verification URL and user code, then poll until
  authorized, denied, cancelled, or expired. `/login xai-api` saves an API key.
- Successful login stores `access` / `refresh` / `expires` under
  `~/.tau/credentials.json`.
- Runtime uses the access token as the Bearer credential for `https://api.x.ai/v1`.
- Refresh happens before expiry. If xAI omits `refresh_token` on refresh, Tau
  keeps the previous refresh token.
- `/logout xai` removes the local credential. It does not remotely revoke the
  grant.
- Print mode works after login: `tau --provider xai --model <id> -p "..."`.

API-key login remains available through `/login xai-api`, `/login` → API key,
`XAI_API_KEY`, or a saved key.

## Security choices

- Device verification URLs are accepted only with an `https` scheme and host.
- Failed OAuth responses may include structured `error` / `error_description`
  text. Request secrets such as refresh tokens are scrubbed from that text.
- Credentials remain in `~/.tau/credentials.json` with mode `0600`. The file is
  not encrypted.
- Tests use `httpx.MockTransport` and fake credentials. CI does not contact
  `auth.x.ai` or require real secrets.

Device-code requests send `referrer=tau`. The current public client ID is the
one `auth.x.ai` already accepts for CLI device login; replace it if xAI issues a
Tau-owned client.

## How to test

```bash
uv run pytest tests/test_oauth_providers.py tests/test_provider_catalog.py::test_builtin_catalog_oauth_and_opencode_auth_methods
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The mocked tests cover successful login with `referrer=tau`, authorization
pending, denial, expiry, untrusted verification URIs, malformed JSON, refresh
token rotation, and omitted `refresh_token` on refresh.

Live subscription smoke tests are not automated. Before a release, verify
`/login xai` once with a SuperGrok or X Premium account, including one
headless/SSH device-code path. Do not copy tokens, device codes, or credential
file contents into GitHub.

## Rollback

Remove `XaiOAuthProvider` from `oauth_registry.py` and drop `oauth` from the xAI
catalog `auth_methods`. Existing local OAuth objects remain parseable so users
can `/logout xai` or keep using `XAI_API_KEY`.
