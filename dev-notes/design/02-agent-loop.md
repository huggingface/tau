---
title: "02 — Agent Loop"
---

The agent loop is the small, reusable engine that turns messages, tools, and provider streams into progress events.

## Responsibilities

1. Receive the current system prompt, transcript, tools, and model selection.
2. Ask the provider to stream a response.
3. Emit events as text and tool calls arrive.
4. Collect the assistant message.
5. Execute requested tools.
6. Append tool results.
7. Continue until the assistant produces no more tool calls.

## Non-responsibilities

The loop does not know about CLI arguments, Textual widgets, session file locations, or project resource discovery.
Those belong in `tau_coding`.

## Event-first design

Every meaningful step should be observable through events so print mode, Rich rendering, and Textual can share the same core.
