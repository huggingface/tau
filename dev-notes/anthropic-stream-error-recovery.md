# Anthropic in-stream error recovery

## What changed

The Anthropic adapter now retries transient errors delivered inside an HTTP 200
Server-Sent Events response. It recognizes Anthropic's `api_error`,
`overloaded_error`, and `rate_limit_error` types and uses the provider's existing
retry count, backoff delay, progress event, and cancellation behavior.

## Why it exists

A production Claude Opus 5 session ended immediately after Anthropic sent an
`overloaded_error` SSE event. The provider was configured for two retries, but
the old adapter applied that budget only to retryable HTTP statuses and transport
exceptions. Every SSE `error` event was terminal, even before any response
content arrived.

## Architecture

Retry classification remains in `tau_ai.anthropic`, where the provider-specific
error type is available. The adapter wraps final stream diagnostics as an
`event` plus the number of attempts, matching the safe extraction performed by
`tau_coding.diagnostics`. The portable agent layer does not gain Anthropic-specific
logic.

Only errors received before text, thinking, or tool payload starts are retried.
Once partial content exists, Tau surfaces the error rather than risking duplicate
output or tool execution.

## How to test

```bash
uv run pytest tests/test_tau_ai.py -k anthropic_provider
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

A manual check can use an Anthropic provider with `max_retries` greater than zero.
During a brief overload before any response content, Tau should retry quietly and
continue when a later attempt succeeds. Persistent overloads should surface the
final `Overloaded` error after the configured attempts are exhausted.
