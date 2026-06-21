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

## LLM request observations

Tau can write an opt-in, redacted JSONL trace of provider HTTP requests and
response metadata. This is useful when debugging model selection, prompt
construction, tool schemas, provider URLs, retry behavior, or provider error
bodies.

Enable it for one command:

```bash
TAU_LLM_OBSERVABILITY=1 tau --provider local -p "summarize this project"
```

or set the same environment variable before starting the TUI.

Records are appended to:

```text
~/.tau/logs/llm-observations.jsonl
```

The log captures the adapter name, model, method, URL, retry attempt, streaming
flag, redacted headers, redacted serialized request body, HTTP status, redacted
response headers, and redacted HTTP/network errors. Streaming response chunks
are not logged in this first version.

Redaction is mandatory. Credential-like headers are replaced with `[REDACTED]`.
Prompt-like text fields such as `content`, `text`, `instructions`, `system`,
`output`, `arguments`, and error `body` are replaced with length and SHA-256
metadata instead of raw text. The file can still reveal sensitive diagnostic
context, so keep it local unless intentionally sharing it.

See [LLM API Observability](architecture/llm-observability.md) for the
architecture and privacy notes.

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
/reload
```

`/model` opens the interactive model picker. The picker includes models from
configured providers, so selecting a model can also switch the active runtime
provider. `/login` adds or refreshes a built-in provider, including
OAuth-backed providers such as `openai-codex`, and `/reload` refreshes provider
settings for future command use.

When Tau loads `~/.tau/providers.json`, it merges the current built-in model
catalog into built-in provider entries such as Hugging Face. Custom models and
headers in the file are preserved.

## Fake provider

Tau also includes `FakeProvider` for deterministic tests. It replays scripted provider events and never makes network requests.

It is used heavily by agent-loop, session, command, and TUI tests.
