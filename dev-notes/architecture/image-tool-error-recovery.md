# Recovering rejected image tool results

## Behavior

Even after the read tool's local resizing, a provider can reject an image or a
batch of images. Previously, the assistant received no useful tool feedback and
the run ended. The portable agent loop now recovers empty provider failures
immediately following an image-bearing tool batch:

1. Persist the failed assistant message and original tool results unchanged.
2. Project the rejected image results into text-only, `is_error=True` tool
   responses, preserving call IDs, textual output, and tool metadata. Include the
   provider's error and suggest a smaller image or another approach.
3. Continue the provider loop without re-executing any tools. Stop on the third
   consecutive failed provider response. A successful response resets the count.

Recovery is deliberately best-effort rather than tied to vendor-specific image
error strings. An outage after an image tool response is also bounded by the
same three-failure limit. Existing adapter-level network retries remain separate.
Errors without a recent image tool batch, partial streamed output, and aborted
requests retain their terminal behavior. Cancellation and `max_turns` still win.

## Architecture and replay

This follows Pi's brain/environment/frontend separation: `tau_agent` owns
provider-neutral tool feedback and bounded loop control, `tau_ai` retains wire
serialization/retries, and `tau_coding` presents recovery progress.

`_provider_context` derives the safe context from durable failed assistant turns.
It copies only rejected tool results and never mutates the original messages.
This preserves append-only JSONL persistence and makes replay/resume reconstruct
the same safe context. Only images from the latest tool batch are removed;
previously accepted images and text-only results stay intact. For a rejected
batch, all its image results are downgraded because a provider-neutral error
cannot reliably identify which attachment caused the rejection.

Before emitting a recoverable failure's `message_end`, the loop adds a
`tool_result_recovery` assistant diagnostic. Frontends render that failure as
progress rather than a terminal error, print renderers do not fail the command,
and coding-session orchestration does not start overflow compaction or route
failover for that intermediate failure. The final failed request has no recovery
diagnostic and uses normal terminal error handling.

## Validation

```bash
uv run pytest tests/test_image_tool_recovery.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
cd website
npm ci
npm run check
npm run format:check
npm run build
npm test
```

The focused tests use fake provider errors, including an outage lasting beyond
three requests, multi-image results, successful counter resets, cancellation,
partial output, max-turn limits, session JSONL reload, print-mode status, and TUI
live/restored state.

For manual validation, use a vision model/provider with a restrictive image or
request-size limit, ask Tau to read an image that exceeds that provider's limit,
and confirm recovery progress followed by a model response that quotes the API
error or chooses another approach. With a provider unavailable after an image
read, verify that the run stops after three high-level provider failures rather
than retrying forever. No live credentials are required by the regression tests.
