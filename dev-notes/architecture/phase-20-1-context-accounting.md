---
title: "Phase 20.1: Context Accounting Refresh"
---

Phase 20.1 tightens Tau's context accounting and makes TUI refreshes follow the
same event stream that updates the active agent transcript.

## What Was Added

`tau_coding.context_window` now exposes `ContextUsageEstimate`, a structured
snapshot with:

```text
total_tokens
system_tokens
message_tokens
tool_tokens
message_count
tool_count
```

`CodingSession.context_usage` returns that snapshot for the active provider
context. The older `context_token_estimate` property remains as a compatibility
shortcut for commands, widgets, and tests that only need the total.

`/status` still prints:

```text
Estimated context tokens: <count>
```

It also includes a stable token breakdown for system prompt, messages, and tool
definitions.

## Prompt Message Events

Prompt runs now emit portable message events for the user prompt:

```text
agent_start
turn_start
message_start user
message_end user
...
```

This mirrors Pi's loop behavior: the prompt is added to context, then the event
stream reports that message before assistant streaming begins. Direct low-level
`run_agent_loop()` callers are unchanged; they still pass an already prepared
message list and receive assistant/tool loop events only.

## TUI Refresh Boundary

The Textual app no longer pre-adds submitted user prompts to the visible
transcript. Instead, `TuiEventAdapter` renders user, assistant, and tool
messages from the streamed events. Because `_refresh()` runs after each event,
the sidebar and compact session line now observe context changes after:

- the submitted user message
- assistant message completion
- tool result insertion
- manual or automatic compaction
- session resume/new-session replacement

This keeps the reusable `tau_agent` package UI-free: it only emits portable
events. Textual-specific rendering remains in `tau_coding.tui`.

## Tests

The phase is covered by:

```text
tests/test_agent_harness.py
tests/test_context_window.py
tests/test_coding_session.py
tests/test_tui_adapter.py
tests/test_tui_app.py
```

The tests verify that context usage is recalculated after normal turns,
compaction, and resumed sessions, and that TUI refreshes see the changed context
estimate at each streamed message/tool boundary.
