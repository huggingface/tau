---
title: Providers & models
description: Connect OpenAI, Anthropic, Codex, OpenRouter, Hugging Face, or a local model — and switch models any time.
---

A **provider** is the service hosting AI models; a **model** is the specific one
you talk to. Tau ships with several built-in providers and lets you add your own
OpenAI-compatible endpoints (including local models).

## The fastest setup: `/login`

Start Tau and use `/login` to connect a provider:

```bash
tau
```

```text
/login              # choose a login method
/login openai       # save an OpenAI API key
/login openai-codex # authenticate a Codex/ChatGPT subscription via OAuth
/login nvidia       # save an NVIDIA NIM API key
/login custom       # add an OpenAI-compatible custom provider
```

Built-in providers include **OpenAI**, **Anthropic**, **OpenAI Codex**
(subscription), **OpenRouter**, **Hugging Face**, and **NVIDIA NIM**.
Credentials saved this way live in `~/.tau/credentials.json` (private
permissions). The custom-provider
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

{{% note title="Codex subscription" %}}
`/login openai-codex` opens the OpenAI OAuth flow, listens for the local
callback, and also accepts a pasted redirect URL or code. It refreshes expired
access tokens automatically. It's separate from the API-key `openai` provider.
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
`api_key_env`. Use `/login` for built-in providers or `/login custom` for
OpenAI-compatible custom providers.
