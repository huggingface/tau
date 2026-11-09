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
7. continues according to tool results, queued messages, and the host's optional
   `finish_turn` decision.

That "call a tool, feed the result back, continue" cycle is what lets the model
read a file, see its contents, and then decide what to edit.

## Terminating tool results

Normally a tool call causes another model request. A tool can instead return
`AgentToolResult(..., terminate=True)` to submit a final answer without that
follow-up. As in Pi, a nonempty batch terminates only when **every final result**
opts in, after tool-result hooks. All sibling tools finish first; partial updates
have no effect. Mixed batches continue, and queued steering/follow-up messages
still receive responses. The flag stays in tool-execution events, not transcript
messages. See [final submissions](/guides/extensions/#final-submissions-and-terminating-tools).

## Host-controlled turn finalization

The portable `AgentHarnessConfig` and `run_agent_loop()` accept `finish_turn`.
This callback runs after the assistant and tool results have been appended, but
before `TurnEndEvent`. For example, stop whenever a submission succeeds, even
in a mixed batch and even with queued work:

```python
from tau_agent import AgentHarnessConfig, AgentTurnContext, AgentTurnDecision
from tau_agent.provider import CancellationToken


def finish_turn(
    turn: AgentTurnContext, signal: CancellationToken | None
) -> AgentTurnDecision | None:
    if turn.message.stop_reason in {"error", "aborted"}:
        return None
    if any(
        result.tool_name == "submit_results" and not result.is_error
        for result in turn.tool_results
    ):
        return AgentTurnDecision(action="end")
    return None


config = AgentHarnessConfig(
    provider=provider, model=model, system=system, tools=tools,
    finish_turn=finish_turn,
)
```

The callback may be synchronous or asynchronous. It receives the active
cancellation token (or `None` for a direct loop without one) and an
`AgentTurnContext` with:

- `message`: the finalized assistant response;
- `tool_results`: a tuple of the turn's finalized transcript tool results;
- `messages`: a tuple of the full transcript at this boundary;
- `new_messages`: a tuple of this invocation's messages, including prompt
  messages but excluding earlier history.

These are shallow snapshots: tuples prevent list edits, but their message objects
are shared, not deep-copied. Treat them as read-only.

Return `None` to preserve normal scheduling, or:

- `AgentTurnDecision(action="end")`: emit `turn_end`, then `agent_end` without
  polling steering/follow-up queues again. Queued messages remain available for
  a later run. This is a successful finish, not cancellation.
- `AgentTurnDecision(action="continue")`: ensure one subsequent model request.
  If tools, steering, or a follow-up already select that request, they satisfy
  the decision; there is no extra duplicate request. Otherwise it is a
  context-only request. This can also override a terminating tool batch.

The callback runs for error and aborted responses too, including synthetic
cancellation and turn-limit messages, but their decisions are ignored: those
remain hard exits. Repeated `continue` decisions can run indefinitely unless
`max_turns` or cancellation stops them. Callback exceptions propagate to the host;
they are not a graceful stop mechanism, and the harness resets its running state
on exit.

This is Pi's `finishTurn` scheduling contract with Python names and typed
dataclasses. It is a harness API, not a `CodingSessionConfig` option or extension
hook. No frontend or provider-specific policy is required in the agent core.

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
[Extensions](/guides/extensions/#events) for their complete
payload table.

The final `AssistantMessage` is authoritative: it persists text, thinking, and tool
calls as ordered content blocks. Nested update events provide responsive rendering,
while saved sessions and provider history replay use the finalized structured message.
It also carries optional monotonic response timing: accumulated time awaiting
provider events through completion and through the first text, thinking, or
tool-call output. Measuring only provider-event awaits excludes frontend rendering
and persistence work between stream pulls. Saved usage and timing remain paired for
effective output-speed and TTFT statistics.
Its `provider` field names Tau's configured provider. When a gateway reports a
more specific backend, `response_provider` records the backend that served that
request. For example, Hugging Face responses expose the selected Inference
Provider there while `provider` remains `huggingface`.

Because the contract is *events*, a frontend's job is reduced to: send a prompt,
consume the stream, draw what you see.

→ See [Build your own frontend](/internals/custom-frontend/) for the concrete API, and
[Architecture overview](/internals/architecture/) for where the loop sits.
