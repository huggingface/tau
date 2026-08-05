# Provider-anchored context accounting and overflow recovery

## What changed

Tau now anchors active context accounting to the latest applicable successful
assistant response's provider-reported usage. It estimates only messages and
dynamically added tools after that response. The existing character-based
estimate remains the fallback when no valid provider usage is available.

The TUI also treats a recognized context-overflow response as provisional while
the coding session compacts and retries. It shows recovery progress and only
surfaces the provider error if compaction or retry cannot recover. The low-level
`agent_end` event no longer settles session-driven TUI runs; `agent_settled`
remains the final boundary.

## Why

Character-only accounting can substantially undercount token-dense tool output,
provider framing, and opaque reasoning state. In a production Codex session,
the provider reported almost 397K processed tokens while Tau displayed a much
smaller estimate, so proactive compaction did not run.

A provider overflow is stronger evidence than a local estimate. Tau's existing
overflow path already bypassed the threshold and retried once, but the TUI
rendered its intermediate provider error as terminal before recovery completed.

## Pi mapping

This follows Pi's hybrid accounting in `packages/ai/src/utils/estimate.ts`:
provider usage describes a valid context prefix and deterministic estimation
covers only the trailing messages. Errored, aborted, zero-usage, and stale
pre-compaction responses cannot anchor the count.

It also preserves Pi's lifecycle distinction: `agent_end` closes one low-level
agent run, while `agent_settled` means no compaction, retry, or queued continuation
remains.

## Validation

Run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Manual check:

1. Start a long Codex subscription session and run `/session` after a successful
   turn. Confirm it shows a provider token basis plus estimated trailing tokens.
2. Trigger a context overflow with automatic compaction enabled.
3. Confirm the TUI shows `Context limit reached; compacting and retrying` and no
   terminal error when retry succeeds.
4. Make summarization fail and confirm the original overflow becomes visible.
