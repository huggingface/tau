---
title: Providers & models
description: Connect OpenAI, Anthropic, Codex, OpenRouter, Hugging Face, or a local model — and switch models any time.
---

A **provider** is the service hosting AI models; a **model** is the specific one
you talk to. Tau ships with several built-in providers and lets you add your own
OpenAI-compatible endpoints (including local models).

## The fastest setup: `/login`

Start Tau and use `/login` to connect a built-in provider:

```bash
tau
```

```text
/login              # choose a built-in provider
/login openai       # save an OpenAI API key
/login openai-codex # authenticate a Codex/ChatGPT subscription via OAuth
```

Built-in providers include **OpenAI**, **Anthropic**, **OpenAI Codex**
(subscription), **OpenRouter**, and **Hugging Face**. Credentials saved this way
live in `~/.tau/credentials.json` (private permissions).

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

:::note[Codex subscription]
`/login openai-codex` opens the OpenAI OAuth flow, listens for the local
callback, and also accepts a pasted redirect URL or code. It refreshes expired
access tokens automatically. It's separate from the API-key `openai` provider.
:::

## Choosing and switching models

- **`/model`** — open the picker (lists models across configured providers;
  choosing one can switch the active provider too).
- **`tau -m <model>`** or **`tau --provider <name> -m <model>`** — choose at
  launch.
- **Ctrl+P** — cycle your *scoped* (favorite) models without opening the picker.
  Build the list with `/scoped-models`, or press `Space` on a model in the
  `/model` picker.

## Adding a custom / local provider

Any OpenAI-compatible endpoint works — including local servers like Ollama or
llama.cpp. For one-off local setup, register one with `tau setup`:

```bash
tau --provider local \
  --base-url http://localhost:11434/v1 \
  --api-key-env LOCAL_API_KEY \
  --model qwen \
  setup
```

This writes an entry to `~/.tau/providers.json` and (by default) makes it the
default provider.

For reusable provider definitions, add `~/.tau/provider-catalog.json` or a
project-local `.tau/provider-catalog.json` instead:

```json
{
  "providers": [
    {
      "name": "local-gateway",
      "display_name": "Local Gateway",
      "kind": "openai-compatible",
      "base_url": "http://localhost:11434/v1",
      "api_key_env": "LOCAL_GATEWAY_API_KEY",
      "credential_name": "local-gateway",
      "models": ["qwen-coder"],
      "default_model": "qwen-coder",
      "docs_url": "https://example.test/local-gateway"
    }
  ]
}
```

Tau loads bundled providers first, then user catalog entries, then project
catalog entries. A later entry with the same `name` overrides the earlier one,
so projects can pin endpoints or model lists without a PR.

Run it with:

```bash
tau --provider local
tau --provider local "summarize this project"   # TUI with an initial prompt
tau --provider local -p "summarize this project" # one-shot print mode
```

Provider entries support `headers`, `timeout_seconds`, `max_retries`, and
`max_retry_delay_seconds`. For the full JSON shape (and `thinking_levels` for
custom models), see [Configuration](../reference/configuration.md#providers).

:::tip[Org billing example]
Hugging Face organization billing is just a header on the provider entry:

```json
{ "headers": { "X-HF-Bill-To": "my-org" } }
```
:::

## How credentials are resolved

For a given provider, Tau uses, in order: a stored credential in
`~/.tau/credentials.json`, then the environment variable named by the provider's
`api_key_env`. Use `/login` for built-in providers. Custom/local providers
created with `tau setup` use their configured environment variable until Tau has
a custom-provider credential form.
