---
title: Managing context
description: Keep long sessions working with automatic and manual compaction, and control model effort with thinking modes.
---

A model can only read so much text at once — its **context window**. Long coding
sessions fill it up. Tau handles this with **compaction** (summarizing older
history) and lets you tune how hard the model works with **thinking modes**.

## Seeing context usage

The compact status below the TUI prompt shows the approximate active context as
`used/limit`. Run `/session` to see its detailed breakdown:

```text
Estimated context tokens: <count>
Context window: <count>
Context window source: configured catalog | provider live catalog
Context token breakdown: system=<count>, messages=<count>, tools=<count>
Thinking mode: <mode>
```

The estimate is deterministic (roughly `characters / 4` plus small per-message
and per-tool overhead), not a provider tokenizer — treat it as approximate. It
covers the system prompt, project context (`AGENTS.md`), skill metadata, the
active message history, and tool schemas.

This is different from **cumulative usage** in the sidebar. Cumulative usage adds
the provider-reported input and output tokens from every request on the active
branch, including history later replaced by compaction. Repeatedly sending the
same context therefore increases cumulative input usage, while active context
consumption describes only what Tau expects to send next. The two figures are
not expected to match.

## Automatic compaction

By default, Tau compacts automatically when the estimate gets close to the
model's context window. It checks three moments:

- before a new prompt (to catch context added out-of-band),
- after a successful turn (to compact before your next turn), and
- after a context-overflow error (compact and retry once).

When it compacts, Tau asks the model to summarize older messages, keeps a recent
suffix of the conversation, and continues. The original session file is never
edited — only the *active context* sent to the provider changes.

The default threshold follows the model's context window minus a reserve. Providers
that advertise an explicit runtime threshold can override that default. In particular,
Codex subscription sessions discover account/rollout-specific limits from Codex's
authenticated model catalog because those limits can differ from the public OpenAI API.
You can override the resulting threshold for a run:

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
- **Ctrl+T** toggles whether reasoning tokens are shown (hidden by default).
  Reasoning blocks are saved with the assistant response, so their original
  positions and visibility toggle are restored when you resume a session.

Thinking is model-aware: Tau enables it only when the active provider declares
supported levels for the active model. When it's unavailable, `/session` shows
the reason (e.g. the provider doesn't declare `thinking_levels`, or the model
isn't listed). Custom providers can opt in via `thinking_levels` in their config
— see [Configuration]({{< relref "../reference/configuration.md#providers" >}}).

At startup Tau picks a valid level for the selected model automatically: a
remembered per-model choice wins, then `medium`, then the provider's own
default, then the first level the model supports. So a model that only supports
`xhigh` (for example `kimi-code:k3`) opens at `xhigh` instead of failing with
"Thinking mode medium is not available". Picking an unsupported level
explicitly (via `/think` or the thinking picker) still shows an error listing
the available modes.
