---
title: Managing context
description: Keep long sessions working with automatic and manual compaction, and control model effort with thinking modes.
---

A model can only read so much text at once — its **context window**. Long coding
sessions fill it up. Tau handles this with **compaction** (summarizing older
history) and lets you tune how hard the model works with **thinking modes**.

## Seeing context usage

Run `/session` in the TUI to see a rough estimate:

```text
Estimated context tokens: <count>
Context token breakdown: system=<count>, messages=<count>, tools=<count>
Thinking mode: <mode>
```

The estimate is deterministic (roughly `characters / 4` plus small per-message
and per-tool overhead), not a provider tokenizer — treat it as approximate. It
covers the system prompt, project context (`AGENTS.md`), skill metadata, the
message history, and tool schemas.

## Automatic compaction

By default, Tau compacts automatically when the estimate gets close to the
model's context window. It checks three moments:

- before a new prompt (to catch context added out-of-band),
- after a successful turn (to compact before your next turn), and
- after a context-overflow error (compact and retry once).

When it compacts, Tau asks the model to summarize older messages, keeps a recent
suffix of the conversation, and continues. The original session file is never
edited — only the *active context* sent to the provider changes.

The default threshold follows the model's context window minus a reserve. You can
override it for a run:

```bash
tau --auto-compact-threshold 100000
```

Automatic compaction is best-effort: if summarization fails, Tau logs it, keeps
the original context, and carries on.

## Manual compaction

Compact on demand any time:

```text
/compact
/compact focus on the database migration work
```

Optional text after `/compact` is added as extra focus for the summary. Manual
compaction summarizes the whole active context into one summary and fails visibly
if the request fails.

## Thinking modes

Some models can spend extra effort reasoning before answering. Tau exposes a
thinking level you can cycle:

```text
off → minimal → low → medium → high → xhigh
```

- **Shift+Tab** cycles the thinking level (default is `medium`).
- **Ctrl+T** toggles whether streamed reasoning tokens are shown (hidden by
  default).

Thinking is model-aware: Tau enables it only when the active provider declares
supported levels for the active model. When it's unavailable, `/session` shows
the reason (e.g. the provider doesn't declare `thinking_levels`, or the model
isn't listed). Custom providers can opt in via `thinking_levels` in their config
— see [Configuration]({{< relref "../reference/configuration.md#providers" >}}).
