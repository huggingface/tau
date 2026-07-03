---
title: "Config-driven provider catalog"
---

Issue: https://github.com/huggingface/tau/issues/238

## What changed

Tau's provider catalog is now data-driven. Bundled provider definitions live in:

```text
src/tau_coding/data/provider_catalog.json
```

At runtime, Tau also reads optional local catalog files:

```text
~/.tau/provider-catalog.json
<project>/.tau/provider-catalog.json
```

The load order is bundled catalog, user catalog, then project catalog. Later
entries override earlier entries with the same provider `name`.

## Why it exists

Before this change, adding a built-in provider or changing a provider's model
metadata required editing Python source. That made provider support PR-driven.
The catalog file keeps source code responsible for validation and runtime
boundaries, while provider metadata can be edited as config.

## Architecture boundary

The implementation stays in `tau_coding` because it deals with Tau home,
project-local files, login lists, and provider preferences. The reusable
`tau_agent` harness still receives only a ready model provider and model name.
The `tau_ai` layer remains focused on provider-neutral runtime adapters.

## Catalog shape

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

Supported `kind` values are `openai-compatible`, `anthropic`, and
`openai-codex`. For user-defined providers, `openai-compatible` is the intended
first path.

## How to test

```bash
uv run pytest tests/test_provider_config.py
uv run pytest tests/test_provider_runtime.py
```

The provider config tests cover bundled catalog loading, user-defined provider
loading, project overrides, invalid catalog validation, and conversion from
catalog entries to durable provider settings.
