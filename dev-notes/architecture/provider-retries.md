---
title: "Provider Retry Events"
---

Tau retries transient provider failures in `tau_ai`, where HTTP status codes and
transport exceptions are visible. This keeps retry classification out of
`tau_agent` while still allowing the portable agent loop to surface progress.

## What Was Added

Provider adapters can emit `ProviderRetryEvent` before retrying a failed request.
The event includes the next attempt number, total attempts, delay, a
human-readable message, and structured diagnostic data.

`run_agent_loop()` maps that provider event to `RetryEvent`, a provider-neutral
agent event consumed by renderers and TUI adapters.

## Behavior

OpenAI-compatible, Anthropic, and OpenAI Codex subscription providers retry
transient status codes such as `408`, `409`, `429`, and `5xx` responses before
surfacing a final provider error. The default is two retries, for three total
request attempts.

The OpenAI Codex adapter also retries transient *in-stream* failures. The Codex
Responses endpoint can return HTTP 200 and then send an SSE `error` or
`response.failed` event (for example `server_is_overloaded`). When such an event
arrives before any content or thinking deltas, the adapter classifies it against
transient markers (overloaded, service unavailable, rate limit, internal/server
errors, timeouts), emits `ProviderRetryEvent`, and reissues the request under
the same `max_retries` budget. Errors after partial content, and non-transient
errors such as `invalid_api_key`, stay terminal.

Backoff is short, exponential, and capped by `max_retry_delay_seconds`.
Cancellation is checked during the backoff delay so Escape/TUI cancellation does
not wait for the entire retry sleep to finish.

## Rendering

Transcript and TUI renderers show retry progress as subtle status output. Final
text mode ignores retry progress and only prints the final assistant response or
final error.

## Boundary

`tau_agent` does not decide whether an HTTP response is retryable. It only
forwards `RetryEvent` as portable progress. Provider-specific details stay in
the adapter's `data` payload for diagnostics.
