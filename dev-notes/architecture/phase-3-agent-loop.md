---
title: "Phase 3: Pure Agent Loop"
---

Phase 3 adds Tau's first real agent engine: a pure loop that connects a model provider, a transcript, and a set of tools.

The loop lives in:

```text
src/tau_agent/loop.py
```

It is called “pure” because it does not know about the CLI, Rich, Textual, slash commands, local session files, or project-specific resources. It only works with provider-neutral Tau types.

## What was added

Phase 3 added:

- `run_agent_loop()`
- transcript mutation for assistant messages and tool results
- provider-event to agent-event conversion
- sequential tool execution
- unknown-tool handling
- tool-exception handling
- provider-error handling
- optional max-turn protection

Later hardening added queue-drain hooks for steering/follow-up prompts and
provider-neutral forwarding for retry and thinking/reasoning progress events.

## The loop's inputs

`run_agent_loop()` receives:

- a `ModelProvider`
- a model name
- a system prompt
- a mutable list of `AgentMessage` objects
- a list of registered `AgentTool` objects
- an optional maximum turn count
- an optional cancellation token
- optional steering/follow-up queue drain callbacks
- an optional queue-state callback for `QueueUpdateEvent`

Conceptually:

```python
async for event in run_agent_loop(
    provider=provider,
    model="...",
    system="...",
    messages=messages,
    tools=tools,
):
    ...
```

The caller owns the transcript. The loop appends to that transcript as work completes.

## Basic text-only flow

For a normal response with no tool calls, the flow is:

```text
agent_start
turn_start
message_start
message_delta
message_delta
message_end
turn_end
agent_end
```

Internally:

1. The loop asks the provider to stream a model response.
2. Provider text deltas become `MessageDeltaEvent`s.
3. The final provider response becomes an `AssistantMessage`.
4. The assistant message is appended to the transcript.
5. The loop stops because there are no tool calls.

## Tool-call flow

If the assistant asks for tools, the loop executes them before stopping.

Example assistant message:

```python
AssistantMessage(
    content="I'll inspect that file.",
    tool_calls=[
        ToolCall(id="call-1", name="read", arguments={"path": "README.md"})
    ],
)
```

The loop then:

1. finds the registered `AgentTool` named `read`
2. emits `tool_execution_start`
3. executes the tool asynchronously
4. emits `tool_execution_end`
5. appends a `ToolResultMessage`
6. starts another model turn

That second model turn sees the updated transcript:

```text
UserMessage
AssistantMessage with tool call
ToolResultMessage
```

and can produce a final answer.

## Why the loop mutates the transcript

The loop accepts a mutable `messages` list and appends new messages to it.

This keeps the loop mostly stateless. Later, `AgentHarness` can own the transcript and pass it into the loop. The loop does not need to know where messages came from or how they will be persisted.

## Provider events vs agent events

Phase 2 introduced provider events such as:

- `response_start`
- `text_delta`
- `response_end`
- `error`

Phase 3 converts those into higher-level agent events:

```text
ProviderResponseStartEvent  -> MessageStartEvent
ProviderTextDeltaEvent      -> MessageDeltaEvent
ProviderResponseEndEvent    -> MessageEndEvent
ProviderErrorEvent          -> ErrorEvent
```

This means UI layers can listen to agent events without knowing about provider internals.

Later provider adapters also translate:

```text
ProviderRetryEvent         -> RetryEvent
ProviderThinkingDeltaEvent -> ThinkingDeltaEvent
```

The loop forwards those events without embedding provider-specific payloads in
the portable agent layer.

## Queued steering and follow-ups

`AgentHarness` owns prompt queues, but `run_agent_loop()` owns the injection
point. When queue callbacks are provided:

- steering messages drain after the current assistant turn and any tool batch
- follow-up messages drain when the run would otherwise stop
- drained messages are appended to the transcript as normal user messages
- the loop emits `MessageStartEvent(message_role="user")` and `MessageEndEvent`
  for each injected user message before the next provider call
- the loop emits a `QueueUpdateEvent` after draining so frontends can update
  pending-message status

Direct callers that do not pass queue callbacks keep the original behavior.

## Error handling

The loop currently handles common failure cases explicitly.

### Unknown tools

If the model requests a tool that was not registered, the loop records a failed tool result:

```text
Unknown tool: tool_name
```

This result is appended as a `ToolResultMessage`, so the model can recover on a later turn.

### Tool exceptions

If a tool raises an exception, the loop catches it and turns it into a failed `AgentToolResult`.

Tools are an isolation boundary. A broken tool should not crash the whole loop by default.

### Provider errors

If the provider emits an error, the loop emits an agent `error` event and stops the run.

### Max turns

Like Pi, Tau's loop does not impose a default turn limit. It continues until the assistant stops requesting tools or another normal stop condition occurs.

Callers that want a safety cap can pass `max_turns`. If that configured limit is reached, the loop emits a recoverable error.

## Important boundary

The loop executes registered tools, but it does not define coding tools itself.

That means:

```text
tau_agent.loop:
  knows how to execute an AgentTool

tau_coding tools:
  know how to read files, write files, edit files, or run bash
```

This keeps Tau's reusable agent package independent from coding-agent-specific behavior.

## How Phase 3 supports the later layers

### AgentHarness

The harness owns the transcript, cancellation token, listeners, and queued
steering/follow-up prompts. It calls `run_agent_loop()` from methods like
`prompt()` and `continue_()`.

### Built-in coding tools

The coding tools are `AgentTool` instances. The loop executes them without
knowing whether a tool reads files, writes files, edits files, or runs a shell
command.

### Print-mode CLI and TUI

Renderers and the Textual TUI consume `AgentEvent`s from the loop and decide how
to display streamed text, tool activity, retry status, thinking deltas, queue
state, and errors.

### Sessions

Session storage persists the transcript messages that the loop appends, including
tool results and queued user prompts after they are injected.

## Design rule

The agent loop should only coordinate provider streams, messages, tools, and events.

If behavior requires CLI flags, terminal rendering, local config paths, slash commands, or project resources, it belongs outside `tau_agent.loop`.
