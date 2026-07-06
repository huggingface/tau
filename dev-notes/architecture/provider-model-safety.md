# Provider/model safety and HTTP error details

Issue #226 overlaps with the provider/model mismatch fixed by PR #249: `/tree`
navigation now preserves the active runtime model instead of replaying only the
historical model from the selected branch. This note documents the additional
hardening added afterward.

## What changed

- Provider/model selection now validates that the selected model is declared by
  the chosen provider before print mode, TUI startup, model switching, provider
  refresh, or runtime provider construction proceeds.
- Sessions with older persisted `model_change` entries that no longer match the
  active provider fall back to the current provider's configured default instead
  of creating a mismatched runtime provider.
- TUI resume heuristics only infer a provider from a saved model when that
  provider is currently usable, so an unavailable provider with the same model
  name cannot win accidentally.
- OpenAI-compatible, OpenAI Codex, and Anthropic HTTP errors now share a helper
  that extracts useful provider error details from JSON bodies and includes the
  HTTP status and selected model in the user-visible provider error message.

## Why it exists

Tau's provider catalog is intentionally provider-specific: the `openai` API key
provider and the `openai-codex` subscription provider are separate transports and
may not support the same models. Validating the model against the active provider
turns invalid combinations into immediate, actionable configuration errors.

The shared HTTP error formatter keeps production failures debuggable when a
provider rejects a request for account, model availability, request shape, or
other validation reasons. The coding-session diagnostic log already records the
provider name, model, status, and safe response body for non-recoverable provider
errors.

## How to test

```bash
uv run pytest tests/test_provider_config.py tests/test_provider_runtime.py \
  tests/test_tau_ai.py tests/test_coding_session.py tests/test_tui_app.py
```

Run the full suite before merging:

```bash
uv run ruff check
uv run pytest
```
