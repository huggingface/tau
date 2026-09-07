# Provider settings v2 migration

## What changed

Tau now writes `~/.tau/providers.json` as a versioned, preferences-only document.
The effective provider catalog remains the source of truth for model lists,
context windows, transports, model metadata, and thinking capabilities.

When Tau loads the old `providers` array format, it immediately:

1. loads the current bundled catalog plus the user's catalog overlay;
2. rebuilds known providers from those current definitions;
3. copies only runtime preferences such as the selected model, headers, retry
   settings, and valid per-model thinking defaults;
4. moves providers absent from the catalog into `~/.tau/catalog.toml`;
5. atomically rewrites `providers.json` with `schema_version: 2`; and
6. retains the original file as `providers.json.bak`.

Stale scoped-model and thinking-default references are removed during migration.

Provider catalogs also support additive `removed_models` tombstones. Tau applies
them after merging built-in and user catalogs, removing matching model,
context-window, metadata, thinking, and default references. This handles stale
user overlays that were written before Tau stopped advertising a model for a
provider. The first tombstone removes the API-only `gpt-5.6` alias from
`openai-codex` without affecting `openai:gpt-5.6`.

## Why

Legacy settings stored complete snapshots of built-in providers. A later Tau
release could add a model while the old snapshot still supplied an outdated
`thinking_models` list or other capability metadata. The model would then appear
to have unavailable thinking controls despite the bundled catalog supporting
it.

Provider definitions and user preferences have different lifecycles. Catalog
metadata should update with Tau, while preferences should survive updates. The
v2 boundary makes that ownership explicit and avoids silently moving stale
built-in snapshots into a user catalog overlay.

## Architecture

This stays in `tau_coding`, the application configuration layer. `tau_ai` and
`tau_agent` remain independent of user file locations and migration policy.
Runtime provider objects are still assembled from catalog definitions plus
preferences before they reach the provider streaming layer.

## Validation

Run:

```bash
uv run pytest tests/test_provider_config.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

For a manual check, start Tau with a legacy `providers.json` containing stale
built-in model or thinking metadata. The current catalog capabilities should be
available, the file should become schema v2, and the untouched legacy document
should remain in `providers.json.bak`.
