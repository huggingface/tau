# Codex in-stream error recovery

## What changed

The OpenAI Codex provider now surfaces the real failure behind SSE `error`
events, retries transient ones automatically, and records the provider's error
classification in the diagnostic log. After a terminal provider error, the TUI
also states that the run ended and can be retried by sending another message.

## Why it exists

A production Codex session failed twice with
`Error: OpenAI Codex returned an error` and no further detail. The session file
showed the provider had sent an HTTP 200 stream containing:

```json
{"type":"error","error":{"type":"service_unavailable_error","code":"server_is_overloaded","message":"Our servers are currently overloaded. Please try again later.","param":null},"sequence_number":2}
```

Three gaps compounded:

1. `_error_message()` only read top-level `message`/`code` fields, so nested
   `error.message` text never reached the user — only the generic fallback did.
2. In-stream errors on HTTP 200 were always terminal; the retry loop only
   covered HTTP statuses and transport exceptions, so a brief overload window
   ended the run immediately.
3. `agent-calls.jsonl` recorded only the (generic) error message, dropping the
   nested `type`/`code` that explain the failure.

## Architecture

The fix preserves Tau's layer boundaries:

- `tau_ai.openai_codex` extracts `(code, message)` from all Codex error shapes
  (top-level fields, nested `error`, and `response.error` for
  `response.failed`). Transient in-stream errors that arrive before any content
  or thinking deltas are retried under the existing `max_retries` budget, with
  the usual backoff, `ProviderRetryEvent` progress, and cancellation checks.
  Retry classification stays in the provider adapter; `tau_agent` only forwards
  events.
- `tau_coding.diagnostics` copies only non-secret scalar fields (`type`,
  `code`, `message`, `sequence_number`, status code, attempt count) from the
  provider event into `agent-calls.jsonl`. Bodies and full payloads stay out.
- `tau_coding.tui.app` appends "Run ended before completion. Send a message to
  retry." to terminal error blocks, except for context-overflow errors, which
  Tau already auto-compacts and retries.

## How to test

```bash
uv run pytest tests/test_tau_ai.py -k codex
uv run pytest tests/test_coding_session.py -k stream_error
uv run pytest tests/test_tui_app.py -k prompt_worker
```

Manual check: configure `openai-codex` with `max_retries: 2` and prompt during
a Codex overload window. Tau retries quietly, and if the overload persists the
error block shows the provider's own message plus the log path and retry hint.
