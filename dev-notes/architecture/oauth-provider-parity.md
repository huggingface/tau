# OAuth provider parity and login production journal

Issue: [#370](https://github.com/huggingface/tau/issues/370)

## What this phase adds

Tau's original OAuth code was tied directly to OpenAI Codex. This phase adds a
small provider-neutral registry in `tau_coding`, ports the two broadly useful Pi
subscription providers that Tau was missing, and adds the current OpenCode
products using their supported API-key mechanism:

- Anthropic Claude Pro/Max OAuth (authorization code + PKCE)
- GitHub Copilot OAuth (device authorization, including GitHub Enterprise
  domain input)
- existing OpenAI Codex OAuth through the same registry
- OpenCode Go and OpenCode Zen catalog entries (`OPENCODE_API_KEY`, not OAuth)

The registry can also be extended by application extensions without adding
provider policy to `tau_agent`.

## Audited upstream snapshot

The parity audit is pinned to Pi commit
[`dcfe36c79702ec240b146c45f167ab75ecddd205`](https://github.com/earendil-works/pi/commit/dcfe36c79702ec240b146c45f167ab75ecddd205),
committed 2026-07-14. The relevant source is under
`packages/ai/src/utils/oauth/`, while Pi's provider guide is
[`packages/coding-agent/docs/providers.md`](https://github.com/earendil-works/pi/blob/dcfe36c79702ec240b146c45f167ab75ecddd205/packages/coding-agent/docs/providers.md).

| Provider/product | Upstream auth | Tau decision | Notes |
| --- | --- | --- | --- |
| OpenAI Codex | OAuth browser callback; Pi also has device code | Ship existing browser flow through registry | Existing Tau credentials remain compatible. Device code remains a follow-up. |
| Anthropic Claude Pro/Max | OAuth authorization code + PKCE | Ship | OAuth requests use Bearer auth, Claude Code identity headers/betas, and the required identity system block. Anthropic currently describes third-party harness use as extra usage billed per token. |
| GitHub Copilot | GitHub device authorization followed by Copilot token exchange | Ship | Supports github.com and a prompted GitHub Enterprise Server domain. Model availability is account policy dependent. |
| Radius | Gateway-discovered browser/device OAuth | Exclude from Tau core | Radius is Pi's first-party `pi-messages` gateway, not a general provider. Tau should not bind a reusable harness to another product's gateway. |
| OpenCode Go | API key | Ship as API-key provider | Official setup asks users to subscribe in the OpenCode console and copy an API key. |
| OpenCode Zen | API key | Ship as API-key provider | This is pay-as-you-go gateway access; it is not OAuth login. |
| Google Gemini API | API key | Keep existing API-key provider | Gemini CLI's consumer OAuth is a first-party-client login. This phase does not reuse undocumented consumer credentials in a third-party harness. Vertex ADC remains a possible separate ambient-auth feature. |

The OpenCode decisions were checked against the OpenCode `dev` documentation:
[`go.mdx`](https://github.com/sst/opencode/blob/dev/packages/web/src/content/docs/go.mdx)
and
[`zen.mdx`](https://github.com/sst/opencode/blob/dev/packages/web/src/content/docs/zen.mdx).
OpenCode's newer console account OAuth dynamically returns workspace provider
configuration, but OpenCode Go/Zen's documented portable provider credential is
still an API key. Tau therefore does not mislabel either product as OAuth.

## Architecture

```text
Textual OAuthLoginScreen
        │ OAuthLoginCallbacks (URL, device code, prompts, progress)
        ▼
tau_coding.oauth_registry
        │ OAuthProvider protocol
        ├── OpenAI Codex
        ├── Anthropic
        └── GitHub Copilot
        │
        ▼
FileCredentialStore ── OAuthRuntimeCredentialResolver ── tau_ai adapter
```

Responsibilities remain aligned with Tau's package boundaries:

- `tau_coding` owns login UX contracts, provider registry, token refresh, local
  credential storage, and provider setup policy.
- `tau_ai` only knows request-time auth material and protocol-specific header or
  payload behavior.
- `tau_agent` is unchanged and knows nothing about providers, OAuth, Textual, or
  local paths.

`OAuthCredential` now allows an optional `account_id` and JSON-compatible
provider metadata. Existing Codex JSON objects load unchanged. GitHub Enterprise
stores only its normalized domain. Writes use a private temporary file and an
atomic replace, avoiding partially written JSON.

Request-time resolvers refresh expired credentials and save replacements before
the request. This also allows Copilot to derive a per-account API base URL from
the short-lived token.

## Security choices and limitations

- PKCE and state validation are retained for browser callbacks.
- Device verification URLs are accepted only with `http` or `https` schemes.
- Device polling follows RFC 8628 defaults, `slow_down`, expiry, and
  cancellation.
- Provider HTTP failures do not include token response bodies, tokens, or
  authorization codes in user-facing exceptions.
- Credentials remain in `~/.tau/credentials.json` with mode `0600`. OS keyring
  or encrypted-at-rest storage is intentionally deferred; users with a stronger
  local threat model should prefer environment variables or protect their Tau
  home directory.
- `/logout` removes local credentials but does not remotely revoke a provider
  grant. Users can revoke Tau/CLI access in their provider account settings.
- Copilot model support varies by plan, organization policy, and model opt-in.
  The bundled catalog is a candidate list; provider errors should be interpreted
  with those account restrictions in mind.

## Validation and release process

Deterministic tests use `httpx.MockTransport` and fake credentials. They cover:

- Anthropic refresh success and error redaction
- Copilot device login, token exchange, Enterprise routing, untrusted URL
  rejection, and refresh metadata
- RFC 8628 `slow_down` and cancellation
- registry replacement/restoration
- legacy Codex credential loading, extensible metadata, permissions, and atomic
  writes
- login picker separation between OAuth and API-key methods

Run the production checks with:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Live OAuth smoke tests are deliberately not automated because they require paid
personal subscriptions and external browsers. Before a release, maintainers
should manually verify each enabled flow using test accounts, including one
headless Copilot device flow and one pasted browser callback. Sanitized results
belong in the release PR; no token, code, callback query, JWT, or credential
file content should be copied into GitHub.

## Rollback

The registry and provider catalog additions can be reverted without changing
session files. Existing API-key credentials and old Codex OAuth objects retain
their previous format. If a provider changes an endpoint or terms, remove it
from `auth_methods`/the built-in registry while retaining credential parsing so
users can update or delete old local entries safely.

## Follow-ups

- Add OpenAI Codex device-code login and an explicit browser/device selector.
- Add a frontend-neutral non-TUI login command for SSH-only use.
- Consider process-level refresh locking if Tau introduces concurrent processes
  sharing one credentials file; atomic replacement protects file integrity but
  does not serialize competing read-modify-write operations across processes.
- Evaluate OS keyring integration and remote revocation links.
- Consider live Copilot model discovery/filtering after login.
