---
title: Providers & models
description: Connect OAuth subscriptions, API-key providers, or a local model — and switch models any time.
---

A **provider** is the service hosting AI models; a **model** is the specific one
you talk to. Tau ships with several built-in providers and lets you add your own
OpenAI-compatible endpoints (including local models).

## The fastest setup: `/login`

Start Tau and use `/login` to connect a provider. The provider picker includes
a search field, which is especially useful for the longer API-key provider list:

```bash
tau
```

```text
/login              # choose a login method
/login openai       # save an OpenAI API key
/login openai-codex # authenticate a Codex/ChatGPT subscription via OAuth
/login anthropic-subscription # authenticate Claude Pro/Max via OAuth
/login anthropic-api # save an Anthropic API key
/login github-copilot # authenticate GitHub Copilot with a device code
/login opencode-go  # save an OpenCode Go API key
/login nvidia       # save an NVIDIA NIM API key
/login custom       # add an OpenAI-compatible custom provider
```

Built-in providers include **OpenAI**, **Anthropic**, **OpenAI Codex**
(subscription), **GitHub Copilot**, **OpenCode Go**, **OpenCode Zen**,
**Moonshot AI (Kimi)**, **Kimi Code** (subscription), **OpenRouter**, **Hugging Face**,
and **NVIDIA NIM**.

### OAuth subscriptions

Choose **Subscription / OAuth** in `/login` for:

| Tau provider | Login flow | Prerequisite |
| --- | --- | --- |
| `openai-codex` | Browser callback with pasted-code fallback | A supported ChatGPT/Codex subscription |
| `anthropic` | Browser callback with PKCE and pasted-code fallback | Claude Pro/Max with Anthropic extra usage available |
| `github-copilot` | GitHub device code | An active Copilot plan; organization policy must allow the selected model |

GitHub Copilot asks for a GitHub Enterprise Server URL/domain. Leave it blank
for `github.com`. Device login also works in SSH/headless sessions: open the
shown verification URL on any device and enter the displayed code.

Anthropic uses distinct direct-login aliases so the authentication method is
unambiguous: `/login anthropic-subscription` starts OAuth, while
`/login anthropic-api` saves an API key. The top-level `/login` picker still
lists Anthropic under both **Subscription / OAuth** and **API key**. OAuth
subscription requests use Anthropic's required
Claude Code identity and may be billed as extra usage rather than consuming
ordinary Claude plan limits. Check Anthropic's current account terms before
using it.

OAuth tokens refresh automatically. `/logout` removes Tau's local credential,
but does not revoke the grant remotely; use the provider's account settings for
remote revocation.

#### Codex subscription context limits

OpenAI's public API and the ChatGPT/Codex subscription are separate serving
surfaces. A model with the same ID can have a smaller, rollout-specific context
window through Codex OAuth than through an API key. For example, the public
GPT-5.6 Sol API advertises a 1.05M-token window, while Codex has advertised
substantially smaller limits through its authenticated model catalog.

Tau queries that catalog when a Codex session starts and uses the returned
context window and automatic-compaction threshold for the session. If discovery
is unavailable, Tau falls back to conservative Codex-specific values from its
built-in catalog; it does not reuse the public API limit. `/session` reports both
the active value and whether it came from the live provider catalog or Tau's
configured fallback.

Live limits can vary by account or rollout and may change independently of Tau.
A discovery failure is non-fatal: Tau reports it in `/session` and continues with
the fallback. Direct OpenAI API sessions retain the context limits documented on
the API model page.

### OpenCode Go and Zen

OpenCode Go and OpenCode Zen are **API-key providers**, not OAuth providers.
Sign in at the OpenCode console, subscribe to Go or fund Zen, copy the API key,
and then run:

```text
/login opencode-go  # subscription limits; https://opencode.ai/zen/go/v1
/login opencode     # Zen pay-as-you-go; https://opencode.ai/zen/v1
```

Both can also read `OPENCODE_API_KEY`. Tau stores their saved credentials under
separate `opencode-go` and `opencode` names, allowing different keys when
needed. Available models and plan limits change over time; consult the
[OpenCode Go](https://opencode.ai/docs/go) and
[OpenCode Zen](https://opencode.ai/docs/zen) pages for the current list.

### Hugging Face Inference Providers

Log in with `/login huggingface` or set `HF_TOKEN`. Tau's built-in Hugging Face
catalog includes 46 coding-capable models routed through
`https://router.huggingface.co/v1`, including DeepSeek, Gemma, GLM, GPT OSS,
Kimi, Llama, MiniMax, MiMo, Qwen, and Step families. Use `/model` to search the
full list; model availability and the inference provider selected by Hugging
Face can vary over time and by account.

### Moonshot AI API vs. Kimi Code

Both Kimi providers authenticate requests with Bearer API keys; neither uses
OAuth. They are separate because the keys come from different consoles, use
different endpoints, and charge against different billing plans:

| Tau provider | Access and billing | Model | Endpoint | Environment variable |
| --- | --- | --- | --- | --- |
| `moonshotai` | Pay-as-you-go key from the [Kimi Open Platform](https://platform.kimi.ai/console/api-keys) | `kimi-k2.7-code` | `https://api.moonshot.ai/v1` | `MOONSHOT_API_KEY` |
| `kimi-code` | Subscription key from the [Kimi Code console](https://www.kimi.com/code/console) | `k3` or rolling `kimi-for-coding` alias | `https://api.kimi.com/coding/v1` | `KIMI_CODE_API_KEY` |

Kimi K3 uses the `k3` model ID and supports up to a 1,048,576-token context
window on eligible plans. Its reasoning effort is currently fixed at `max`,
which Tau exposes as the `xhigh` thinking level. Start a new session when
switching to K3 so the previous model's context cache is not re-prefilled. See
[Kimi's model documentation](https://www.kimi.com/code/docs/en/kimi-code/models)
for current plan availability and context limits.

A key for one service should not be treated as interchangeable with a key for
the other. Tau stores them independently under the `moonshotai` and `kimi-code`
credential names, so `/login moonshotai` and `/login kimi-code` can configure
both at once. The distinct environment variable names provide the same
separation when credentials are supplied through the shell.

Credentials saved through `/login` live in `~/.tau/credentials.json` with
private `0600` permissions and atomic file replacement. The file is not
encrypted; protect your Tau home directory and do not share its contents. The custom-provider
flow asks for the provider name, display name, base URL, API-key environment
variable, default model, and API key; it writes the provider definition to
`~/.tau/catalog.toml` and runtime preferences to `~/.tau/providers.json`.

Check what's configured and how each provider will authenticate:

```bash
tau providers
```

## Managing saved credentials

Use these slash commands inside Tau:

```text
/login [provider]   # add or refresh a saved credential
/logout [provider]  # remove a saved credential
```

Saved credentials take precedence over environment variables. `/logout` only
edits saved credentials — it never touches your environment or `providers.json`.

{{% note title="OAuth troubleshooting" %}}
Browser login can fall back to a pasted redirect URL/code when the callback
port is unavailable or the browser runs on another machine. Copilot uses a
device code instead. A denied or expired code requires a new `/login`. If a
Copilot model reports that it is unsupported, enable it in Copilot Chat's model
selector or ask your organization administrator; provider/model access varies
by plan and policy.
{{% /note %}}

## Choosing and switching models

- **`/model`** — open the picker (lists models across configured providers;
  choosing one can switch the active provider too).
- **`tau -m <model>`** or **`tau --provider <name> -m <model>`** — choose at
  launch.
- **Ctrl+P** — cycle your *scoped* (favorite) models without opening the picker.
  Build the list with `/scoped-models`, or press `Space` on a model in the
  `/model` picker.

Tau validates the selected model against the active provider's configured model
list before creating or refreshing a runtime provider. This prevents accidental
provider/model mismatches, such as trying to send an API-only OpenAI model to the
separate `openai-codex` subscription provider.

## Adding a custom / local provider

Any OpenAI-compatible endpoint works — including local servers like llama.cpp or
Ollama. The easiest interactive path is:

```text
/login custom
```

Tau prompts for the provider details, saves the API key, writes the provider
metadata to `~/.tau/catalog.toml`, and makes the provider available immediately.

### llama.cpp quickstart

Tau works with llama.cpp through its OpenAI-compatible server. Start a local
server with a GGUF model from Hugging Face:

```bash
llama-server -hf ggml-org/Qwen3.6-35B-A3B-GGUF:Q8_0
```

Some installs expose the same server as `llama serve`:

```bash
llama serve -hf ggml-org/Qwen3.6-35B-A3B-GGUF:Q8_0
```

Then register it with Tau:

```bash
export LLAMA_API_KEY=local # any non-empty value unless you started llama.cpp with --api-key

tau --provider llama-cpp \
  --base-url http://localhost:8080/v1 \
  --api-key-env LLAMA_API_KEY \
  --model local \
  setup
```

Run Tau against the local model:

```bash
tau --provider llama-cpp
tau --provider llama-cpp "summarize this project"    # TUI with an initial prompt
tau --provider llama-cpp -p "summarize this project" # one-shot print mode
```

`llama-server` listens on port `8080` by default and only enforces the bearer
token if you launch it with `--api-key`.

For scripted or one-off setup with another OpenAI-compatible server, use the
same `tau setup` flow. For example, Ollama's OpenAI-compatible endpoint usually
runs at `http://localhost:11434/v1`:

```bash
tau --provider local \
  --base-url http://localhost:11434/v1 \
  --api-key-env LOCAL_API_KEY \
  --model qwen \
  setup
```

This writes the provider definition to `~/.tau/catalog.toml`, writes runtime
preferences to `~/.tau/providers.json`, and (by default) makes it the default
provider.

For reusable provider definitions, add a user-level catalog overlay at
`~/.tau/catalog.toml`:

```toml
schema_version = 1

[[providers]]
name = "local-gateway"
display_name = "Local Gateway"
kind = "openai-compatible"
base_url = "http://localhost:11434/v1"
api_key_env = "LOCAL_GATEWAY_API_KEY"
credential_name = "local-gateway"
models = ["qwen-coder"]
default_model = "qwen-coder"
docs_url = "https://example.test/local-gateway"

[providers.context_windows]
qwen-coder = 64000
```

Tau loads its bundled `src/tau_coding/data/catalog.toml` first, then overlays
`~/.tau/catalog.toml`. A user entry with the same `name` can extend or override a
built-in provider: scalar fields replace built-in values, `models` are merged
with your models first, and `context_windows` are merged.

There is intentionally **no project-level** `.tau/catalog.toml`. Only the
user-level `~/.tau/catalog.toml` is loaded, so cloning a repository cannot
silently redirect a provider's `base_url` or credentials to an unexpected
service.

Run the custom provider with:

```bash
tau --provider local-gateway
tau --provider local-gateway "summarize this project"    # TUI with an initial prompt
tau --provider local-gateway -p "summarize this project" # one-shot print mode
```

Catalog TOML is for provider and model metadata. It does **not** accept runtime
request options such as custom HTTP headers, timeouts, or retry settings. Put
those in `~/.tau/providers.json` instead. Saved `providers.json` entries support
`headers`, `timeout_seconds`, `max_retries`, and `max_retry_delay_seconds`. For
the full JSON shape, the catalog TOML shape, and `thinking_levels` for custom
models, see [Configuration]({{< relref "../reference/configuration.md#providers" >}}).

{{% tip title="Hugging Face org billing" %}}
To send a Hugging Face billing header, keep the provider definition in the
catalog, then add the header to the matching provider preference in
`~/.tau/providers.json`:

```json
{
  "default_provider": "huggingface",
  "provider_preferences": {
    "huggingface": {
      "default_model": "openai/gpt-oss-120b",
      "headers": { "X-HF-Bill-To": "my-org" },
      "thinking_defaults": { "openai/gpt-oss-120b": "low" },
      "timeout_seconds": 60,
      "max_retries": 2,
      "max_retry_delay_seconds": 1
    }
  },
  "scoped_models": []
}
```
{{% /tip %}}

## How credentials are resolved

For a given provider, Tau uses, in order: a stored credential in
`~/.tau/credentials.json`, then the environment variable named by the provider's
`api_key_env`. OAuth credentials are refreshed immediately before a request and
the replacement is saved atomically. Use `/login` for built-in providers or
`/login custom` for OpenAI-compatible custom providers.
