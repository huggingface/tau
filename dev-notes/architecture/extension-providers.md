---
title: "Extension-owned providers"
---

# Extension-owned providers

## What changed

Tau extensions can now register OpenAI-compatible providers, refresh their
models, remove them, and switch the active session through public APIs.
Extensions load before CLI provider/model resolution in TUI and print mode, so
an explicitly loaded provider works with `--provider` and `--model` on the
first process start.

The API also provides user-level JSON settings for non-secret extension state:

- `register_provider(OpenAICompatibleProvider(...))`
- `update_provider_models(...)`
- `unregister_provider(...)`
- `select_model(...)`
- `load_settings()`, `save_settings(...)`, and `clear_settings()`

OpenAI-compatible registrations support required, optional, or absent bearer
authentication. Optional auth omits the Authorization header when the named
environment variable is unset.

## Why it exists

Local servers and third-party gateways often discover models dynamically and
may not require credentials. Previously an extension could build discovery UX
but could not feed the result into Tau's normal model picker or transport. It
also could not satisfy startup `--provider` selection because extensions loaded
only while constructing the coding session, after provider resolution.

This seam keeps provider-specific discovery, commands, diagnostics, and
configuration outside core. Tau owns only registration lifecycle, selection,
user-level non-secret storage, and reuse of the existing OpenAI-compatible
transport. `tau_agent` remains provider-agnostic.

## Lifecycle

Registrations belong to the extension generation. A reload clears outgoing
providers, invalidates stale API objects, then lets the new `setup(tau)` restore
providers from extension settings. Ownership prevents one extension from
updating or unregistering another extension's provider. Setup failures remove
all registrations from the failing extension.

Registrations are process-local rather than written to Tau's provider catalog.
An extension persists its endpoint, models, and selected model in its own
settings and registers them again during setup. Settings always live under the
user Tau home, even for an extension loaded explicitly from a project.

## How to test

```bash
uv run pytest tests/test_extensions.py tests/test_cli.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The tests cover ownership, dynamic model refresh, session selection,
unregistration, settings across reload, and extension registration before
print-mode CLI selection.
