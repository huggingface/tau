---
title: "Phase 22: Compaction Replay Foundation"
---

This phase started Tau's compaction and context-management work. It now includes
model-driven Pi-style summaries, recent-context retention for automatic
compaction, and overflow-triggered retry behavior.

The implementation lives in:

```text
src/tau_agent/session/entries.py
src/tau_agent/session/memory.py
src/tau_coding/context_window.py
src/tau_coding/session.py
src/tau_coding/commands.py
```

## What was added

`CompactionEntry` is now meaningful during session replay.

When `SessionState.from_entries()` sees a compaction entry, it:

1. removes message entries whose ids appear in `replaces_entry_ids`
2. inserts one provider-neutral summary message at the first replaced position
3. keeps any non-replaced recent messages in chronological order
4. keeps the original append-only entries intact

The summary message currently uses this stable form:

```text
Previous conversation summary:
<summary>
```

## Why It Exists

Tau needs compaction to reduce the active provider context while preserving the
full session file. This keeps Pi's append-only session property:

```text
session file = durable history
SessionState.messages = reconstructed active context
```

Manual `/compact` work can append `CompactionEntry` values without editing or
deleting old entries.

## Context Size Estimation

Tau now has deterministic approximate context-size helpers in `tau_coding`:

```python
estimate_text_tokens(...)
estimate_message_tokens(...)
estimate_tool_tokens(...)
estimate_context_tokens(...)
```

These helpers intentionally use a rough character-based estimate instead of a
provider-specific tokenizer. `/status` surfaces the current estimate as:

```text
Estimated context tokens: <count>
```

This gives automatic compaction thresholds a stable application-layer primitive
without adding tokenizer policy to `tau_agent`.

Tau enables Pi-style automatic compaction by default using:

```text
model context window - 16384 reserve tokens
```

Built-in models carry configured context-window metadata, and unknown/custom
models fall back to a `128000` token window. You can override the threshold for a
run with:

```bash
tau --auto-compact-threshold 100000
```

When the active context estimate exceeds the effective threshold before a new prompt or
after a model response, Tau asks the active provider for a structured summary,
appends a `CompactionEntry`, and rebuilds the in-memory transcript. Tau also
attempts one compact-and-retry cycle after provider errors that look like
context overflow.

## Manual Compaction

Tau now supports model-generated manual compaction in the TUI:

```text
/compact [instructions]
```

The command uses Tau's built-in Pi-style compaction prompt. Optional
instructions are appended to that prompt as extra focus. The generated summary is
stored in a `CompactionEntry`, followed by a new leaf pointer. Tau then replays
the session and replaces the in-memory harness transcript for future turns.

See [Context Compaction](../context-compaction.md) for the current prompt,
trigger conditions, and known limitations.

## Boundary

This foundation is in `tau_agent` because replaying session entries is a
portable harness concern. It does not know about slash commands, Textual, Rich,
Tau home paths, token thresholds, or which model creates a summary.

Token estimation, summary generation, overflow classification, and command UX
live in `tau_coding`.

## Tests

The phase is covered by:

```text
tests/test_session.py
tests/test_context_window.py
tests/test_commands.py
tests/test_coding_session.py
tests/test_tui_app.py
```

The tests verify:

- compaction entries round-trip through JSONL
- linear replay replaces compacted messages with a summary
- branch replay applies compaction only on the active branch path
- context-size estimation is deterministic
- `/status` includes an estimated context token count
- `/compact [instructions]` requests model-generated compaction
- manual compaction persists entries and rebuilds future-turn context
- opt-in automatic compaction runs before prompts and after responses when the
  threshold is exceeded
- overflow-triggered compaction retries the provider call once when possible
