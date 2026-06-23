# Providers

Tau's provider layer lives in `tau_ai`.

Providers translate external model APIs into Tau's provider-neutral event stream.

## OpenAI-compatible provider

Tau currently includes an OpenAI-compatible chat completions adapter.

Set:

```bash
export OPENAI_API_KEY="..."
```

Optionally set a custom compatible endpoint:

```bash
export OPENAI_BASE_URL="https://api.openai.com/v1"
```

Optionally tune the HTTP timeout used by the default OpenAI-compatible provider:

```bash
export OPENAI_TIMEOUT_SECONDS="120"
```

Optionally tune transient request retries:

```bash
export OPENAI_MAX_RETRIES="2"
export OPENAI_MAX_RETRY_DELAY_SECONDS="0.5"
```

The provider uses `/chat/completions` with streaming enabled.

## OpenAI Codex Subscription Provider

Tau also includes an `openai-codex` provider for Codex / ChatGPT subscription
accounts. This is separate from the `openai` API-key provider.

In the TUI, run:

```text
/login openai-codex
```

Tau opens the OpenAI OAuth URL, listens for the local callback at
`http://localhost:1455/auth/callback`, and also accepts a pasted redirect URL or
authorization code as a fallback. Refreshable OAuth credentials are saved in
`~/.tau/credentials.json` with private file permissions.

The provider sends requests to the ChatGPT backend Codex Responses endpoint and
uses the OAuth access token plus the ChatGPT account id required by that API.
Expired access tokens are refreshed automatically before a model request.

## Durable Provider Config

Tau stores provider metadata in:

```text
~/.tau/providers.json
```

List configured providers:

```bash
tau providers
```

`tau providers` shows whether each provider will use a stored Tau credential,
an environment-variable fallback, or has missing credentials. Stored API keys
and OAuth credentials in `~/.tau/credentials.json` take precedence over
provider-specific environment variables such as `OPENAI_API_KEY`.

Create or update a provider:

```bash
tau --provider local \
  --base-url http://localhost:11434/v1 \
  --api-key-env LOCAL_API_KEY \
  --timeout-seconds 120 \
  --max-retries 2 \
  --max-retry-delay-seconds 0.5 \
  --model qwen \
  setup
```

Provider entries can also include `headers`, `timeout_seconds`, `max_retries`,
and `max_retry_delay_seconds` in `~/.tau/providers.json`. `headers` must be an
object with string keys and string values. Timeout must be greater than zero;
retry count and retry delay must be zero or greater.

Example:

```json
{
  "name": "huggingface",
  "type": "openai-compatible",
  "base_url": "https://router.huggingface.co/v1",
  "api_key_env": "HF_TOKEN",
  "credential_name": "huggingface",
  "models": ["openai/gpt-oss-120b"],
  "default_model": "openai/gpt-oss-120b",
  "headers": {
    "X-HF-Bill-To": "my-org"
  }
}
```

Run Tau with a configured provider:

```bash
tau --provider local
tau --provider local "summarize this project"
tau --provider local -p "summarize this project"
```

The positional prompt starts the TUI with an initial prompt. The `-p` form runs
non-interactive print mode.

Inside the TUI:

```text
/model
/model qwen
/login
```

`/model` opens the interactive model picker. The picker includes models from
configured providers, so selecting a model can also switch the active runtime
provider. The model flow refreshes provider settings before it validates or
shows choices. `/login` adds or refreshes a built-in provider, including
OAuth-backed providers such as `openai-codex`, and refreshes provider settings
after saving credentials.

When Tau loads `~/.tau/providers.json`, it merges the current built-in model
catalog into built-in provider entries such as Hugging Face. Custom models and
headers in the file are preserved. If `credentials.json` already contains stored
credentials for a built-in provider that is missing from `providers.json`, Tau
adds that provider back in memory so a stale provider file does not hide saved
logins.

When Tau writes provider settings after `/login`, `/model`, or scoped-model
changes, it reloads the latest file first, applies the narrow requested change,
writes atomically, and keeps a best-effort `providers.json.bak` backup.

## Fake provider

Tau also includes `FakeProvider` for deterministic tests. It replays scripted provider events and never makes network requests.

It is used heavily by agent-loop, session, command, and TUI tests.
