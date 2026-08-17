# Provider error recovery and visible TUI failures

## What changed

Tau now keeps empty failed or aborted assistant messages in durable session
history without replaying those messages to providers. The Textual frontend also
projects terminal assistant failures into the mounted transcript immediately,
including any partial assistant text that arrived before the failure.

## Why it exists

A production Kimi session exhausted retries with HTTP 429. Tau persisted the
failure as an assistant message with no content. The user's next prompt replayed
that empty assistant turn through the OpenAI-compatible chat format, and Kimi
rejected the request with HTTP 400. Tau recorded the second failure internally,
but incremental TUI rendering finalized an empty assistant widget instead of
mounting the error item, so the run appeared to stop silently.

## Architecture

The fix preserves Tau's layer boundaries:

- `tau_agent.loop` derives provider-facing context from canonical harness
  history. Empty terminal failures are omitted only at this boundary; the
  original messages remain available to sessions, branches, and diagnostics.
- `tau_coding.tui.state` defines the canonical display projection for failed
  assistant messages: replayable partial text followed by an error block.
- `tau_coding.tui.app` rebuilds the transcript once at the terminal failure
  event. This is not a high-frequency streaming path and guarantees that live
  rendering matches restored session rendering.

Only empty `error` and `aborted` assistant turns are filtered. A failed message
with text, thinking, or tool-call content remains in provider context for now so
this focused fix does not silently discard a partial response. A broader policy
for partially failed turns and unmatched tool calls can be handled separately.

## How to test

```bash
uv run pytest tests/test_agent_loop.py tests/test_tui_adapter.py tests/test_tui_app.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The regression tests prove that an empty failed turn remains in harness history
but is absent from the next provider call, restored failures retain their visible
error, and a mounted Textual transcript shows the provider error during a live
run.
