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
provider-neutral events — never from raw provider chunks. The portable `tau_agent.events.AgentEvent` stream contains:

- `AgentStartEvent` / `AgentEndEvent` — a run begins / ends
- `TurnStartEvent` / `TurnEndEvent` — one assistant response and its tool results
- `MessageStartEvent` / `MessageUpdateEvent` / `MessageEndEvent` — a message's
  Pi-compatible lifecycle
- `ToolExecutionStartEvent` / `ToolExecutionUpdateEvent` / `ToolExecutionEndEvent`
  — a tool runs

Streaming detail is nested under
`MessageUpdateEvent.assistant_message_event`. Those provider-neutral nested
events cover text, thinking, and tool-call start/delta/end updates. Provider
completion or failure is represented by the final assistant message delivered
through `MessageEndEvent`.

`tau_coding.events.CodingSessionEvent` extends that portable stream for
frontends and SDK users with `agent_settled`, queue updates, compaction,
session-entry/session-info changes, thinking-level changes, and automatic-retry
events. Extensions observe those same event names, but the session-to-extension
adapter enriches `turn_start` with a zero-based `turn_index` and millisecond
`timestamp`, and `turn_end` with the matching index. See
[Extensions]({{< relref "../guides/extensions.md#events" >}}) for their complete
payload table.

The final `AssistantMessage` is authoritative: it persists text, thinking, and tool
calls as ordered content blocks. Nested update events provide responsive rendering,
while saved sessions and provider history replay use the finalized structured message.

Because the contract is *events*, a frontend's job is reduced to: send a prompt,
consume the stream, draw what you see.

→ See [Build your own frontend]({{< relref "./custom-frontend.md" >}}) for the concrete API, and
[Architecture overview]({{< relref "./architecture.md" >}}) for where the loop sits.
