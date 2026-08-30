# Hugging Face automatic route failover

## What changed

Tau now distinguishes two session-scoped Hugging Face routing modes:

- `automatic`: an unsuffixed first request becomes sticky after a successful
  `x-inference-provider` response, but the route remains recoverable.
- `fixed`: a route selected through provider preferences or the extension API is
  explicit user intent and is never silently replaced.

When a sticky automatic route exhausts its provider-level retries with HTTP 408,
409, 425, 429, or 5xx before emitting content, the coding session clears the
resolved suffix and continues the same agent run once through Hugging Face's
unsuffixed automatic router. The same recovery applies to HTTP 413: although a
payload rejection is not transient on one backend, another Hugging Face route
may accept that model request. A successful response supplies the replacement
route header, which becomes the new automatic session pin.

## Why core owns recovery

The provider adapter knows how to repeat one HTTP request, but it cannot change
application-owned session metadata or restart the interrupted agent run with a
new runtime. An extension can select a route, but its only public way to trigger
another turn adds a user message. `tau_coding.CodingSession` already owns safe
continuation, persistence, cancellation, auto-retry events, and overflow
recovery, so it is the correct boundary for route failover.

The split remains:

- `tau_ai`: retry the same wire request and preserve provider diagnostics.
- `tau_agent`: portable provider/tool loop with no Hugging Face assumptions.
- `tau_coding`: classify a terminal route failure, replace the runtime, continue
  without another user message, and persist the route mode and result.
- Hugging Face extension: discover live routes and let users choose automatic or
  fixed mode.

## Safety rules

Failover happens only when all conditions hold:

1. the logical Tau provider is `huggingface`;
2. the session mode is `automatic`;
3. a resolved route is currently pinned;
4. provider diagnostics contain a retryable HTTP status or HTTP 413; and
5. the failed assistant message has no text, thinking, or tool-call content.

Only one automatic reroute is attempted per prompt. The fallback itself is not
rerouted again, preventing loops. Fixed routes still receive their configured
same-route retries and then surface the terminal error.

## Persistence and compatibility

Session indexes now store `inference_provider_mode` next to the existing
`inference_provider`. The current route remains a projection used for startup and
resume; logical model identity and transcript entries remain unchanged.

Older records with a route but no mode load as `fixed`. That conservative default
prevents an upgrade from overriding a route that may have been selected manually.
Records without a route load as `automatic`.

The extension API exposes both the current resolved route and its mode. Existing
extensions remain compatible: passing a route to `set_inference_provider` now
means fixed, while passing `None` means automatic.

## User-visible behavior

`/session` reports either:

```text
Hugging Face inference provider: automatic (currently baseten)
Hugging Face inference provider: deepinfra (fixed)
```

Automatic recovery emits `agent_end(will_retry=true)`, `auto_retry_start`, the
continuation events, `auto_retry_end`, and finally `agent_settled`. Print-mode
renderers treat a successful retry as a successful command. The TUI removes the
intermediate terminal error when retry progress begins.

Failover may lose provider-local prefix cache state and require a cold prefill.
It also cannot bypass account-wide or router-wide rate limits. A 413 from the
unsuffixed fallback remains terminal after this single attempt, preventing retry
loops.

## Validation

Deterministic tests cover automatic failover and repinning, exact continuation
context without an extra user message, fixed-route non-failover, legacy session
compatibility, extension-visible mode, frontend retry rendering, and session
metadata round trips.

Run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
