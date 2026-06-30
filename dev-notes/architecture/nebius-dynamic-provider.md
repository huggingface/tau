---
title: "Nebius Token Factory provider and dynamic model listing"
---

This adds Nebius Token Factory as a built-in OpenAI-compatible provider whose
model list is fetched live at build time, instead of being hardcoded.

## Why

Nebius Token Factory exposes an OpenAI-compatible API at
`https://api.tokenfactory.nebius.com/v1` plus a `GET /v1/models` endpoint that
accepts a `verbose=true` query parameter to return the full catalog. Every other
Tau provider ships a hardcoded model tuple in `BUILTIN_PROVIDER_CATALOG`, which
is a poor fit for a catalog that changes frequently. This phase introduces a
dynamic-listing capability and uses Nebius as its first consumer.

## What was added

```text
src/tau_ai/models.py
src/tau_ai/__init__.py
src/tau_coding/provider_catalog.py
src/tau_coding/provider_config.py
src/tau_coding/cli.py
src/tau_coding/tui/app.py
```

- `tau_ai.models.list_openai_compatible_models` — a tolerant
  `GET {base_url}/models` client that sends `verbose=true` when asked, parses
  `{"data": [{"id": ...}]}`, and returns `ModelInfo` objects (with an optional
  best-effort context window).
- `ProviderCatalogEntry.dynamic_models` — a flag (defaults `False`) so only
  providers that opt in fetch their list. The Nebius catalog entry starts with
  empty `models`/`default_model` and `dynamic_models=True`.
- `OpenAICompatibleProviderConfig.dynamic_models` — the durable counterpart,
  serialized as `"dynamic_models": true` in `providers.json`. When set, the JSON
  parser tolerates an empty `models` list and an empty `default_model` (via the
  new `_emptyable_string` helper), so a not-yet-fetched dynamic provider
  round-trips safely. Non-dynamic providers keep their strict validation.
- `ensure_dynamic_provider_models` — a best-effort async helper that, for the
  selected dynamic provider with usable credentials, fetches the verbose model
  list, picks `default_model` as the first returned id, merges any discovered
  context windows, and persists through the existing `upsert_provider` +
  `save_provider_settings` path. Network/auth/parse failures leave settings
  unchanged so startup never fails because of a model listing problem.
- The TUI and print-mode entrypoints call `ensure_dynamic_provider_models`
  before `resolve_provider_selection`, so the live list is in place by the time
  the session/provider is built.

## How it maps to Pi's design

It preserves the `tau_ai` (streaming) / `tau_coding` (catalog + config) split:
the new listing client lives in `tau_ai` next to the other HTTP adapters, while
the catalog flag, durable config field, and build-time wiring live in
`tau_coding`. No existing provider behavior changes; the feature is gated to
`dynamic_models`.

## How to test or use

```bash
export NEBIUS_TOKEN_FACTORY_API_KEY="..."
tau --provider nebius          # TUI; /model shows the live catalog
tau --provider nebius -m <model-id>
```

Tests are mocked (`httpx.MockTransport`) and need no network:

```bash
uv run pytest tests/test_tau_ai.py tests/test_provider_config.py
uv run ruff check
```
