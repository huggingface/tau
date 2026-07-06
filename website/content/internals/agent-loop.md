---
title: The agent loop & events
description: The small engine at Tau's center, and the event stream every frontend renders.
---

The **agent loop** is the small, reusable engine that turns messages, tools, and
provider streams into a flow of progress **events**. It's the part that makes
something an *agent* rather than a chat box.

## What the loop does

For each turn, the loop:

1. takes the current system prompt, transcript, tools, and model selection;
2. asks the provider to stream a response;
3. emits events as text and tool calls arrive;
4. collects the assistant message;
5. executes any requested tools;
6. appends the tool results to the transcript;
7. repeats until the assistant produces no more tool calls.

That "call a tool, feed the result back, continue" cycle is what lets the model
read a file, see its contents, and then decide what to edit.

## What the loop does *not* do

The loop knows nothing about CLI arguments, Textual widgets, session file
locations, or resource discovery. Those belong to `tau_coding`. Keeping them out
is what makes the loop reusable across every frontend.

## Event-first design

Every meaningful step is observable as an event, so print mode, Rich rendering,
and the Textual TUI all share the same core. Frontends render from these
provider-neutral events — never from raw provider chunks. The main event types
include:

- `AgentStartEvent` / `AgentEndEvent` — a run begins / ends
- `MessageStartEvent` / `MessageDeltaEvent` / `MessageEndEvent` — streamed
  assistant text
- `ThinkingDeltaEvent` — optional streamed reasoning (hidden by default)
- `ToolExecutionStartEvent` / `ToolExecutionUpdateEvent` / `ToolExecutionEndEvent`
  — a tool runs
- `QueueUpdateEvent` — pending steering / follow-up prompts
- `ErrorEvent` — recoverable or fatal errors

Because the contract is *events*, a frontend's job is reduced to: send a prompt,
consume the stream, draw what you see.

→ See [Build your own frontend]({{< relref "./custom-frontend.md" >}}) for the concrete API, and
[Architecture overview]({{< relref "./architecture.md" >}}) for where the loop sits.
