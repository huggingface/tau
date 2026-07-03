---
title: "Config-driven provider catalog"
---

Issue: https://github.com/huggingface/tau/issues/238

## What changed

Tau's built-in provider catalog is now data-driven. Provider and model metadata
lives in:

```text
src/tau_coding/data/catalog.toml
```

At runtime, Tau can also overlay a user catalog:

```text
~/.tau/catalog.toml
```

The overlay is optional. It can add a new provider or partially override a
built-in provider.

## Why it exists

Before this change, adding a provider or updating a model list required editing
Python source. That made provider support PR-driven even when the change was only
metadata. Moving the catalog to TOML keeps the source code focused on validation
and runtime behavior while making provider additions small and reviewable.

## Architecture boundary

The implementation stays in `tau_coding` because catalog loading uses Tau home
paths and application-specific provider preferences. The reusable `tau_agent`
harness still receives only a ready model provider and model name. The `tau_ai`
layer remains focused on provider-neutral runtime adapters.

The public compatibility surface remains `ProviderCatalogEntry` in
`tau_coding.provider_catalog`. TOML parsing, validation, and overlay behavior
live in `tau_coding.catalog_loader`.

## Catalog shape

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

Supported `kind` values are `openai-compatible`, `anthropic`, and
`openai-codex`. For user-defined providers, `openai-compatible` is the intended
first path.

## Overlay behavior

When `~/.tau/catalog.toml` defines a provider with the same `name` as a built-in
provider:

- scalar fields replace built-in values
- `models` are merged with user models first
- `context_windows` are merged
- thinking fields replace as a group when `thinking_levels` is present

Tau intentionally supports only a user-level catalog overlay in this phase. It
does not read project-local catalog files, so cloning a repository cannot
silently redirect a built-in provider's `base_url`.

## Validation

Catalog files fail early with `CatalogError`. Tau rejects unknown keys, empty
required strings, empty model names, unsupported provider kinds, default models
that are not listed in `models`, `thinking_models` or `context_windows` entries
for unknown models, and non-positive or non-integer context-window values.

## How to test

```bash
uv run pytest tests/test_provider_catalog.py
uv run pytest tests/test_provider_config.py
uv run ruff check .
uv run mypy
```

The catalog tests cover built-in loading, packaged-resource access, user-defined
providers, built-in overlays, invalid catalogs, and integration with
`ProviderSettings` when `providers.json` already exists.
