---
title: "Phase 1: Core Types and Events"
---

Phase 1 creates the shared vocabulary for Tau's future agent loop.

Before Tau can talk to a model, execute tools, save sessions, or render a UI, every layer needs to agree on the shape of the data flowing through the system. That is what the core message, tool, JSON, and event types provide.

## What was added

Phase 1 added four modules in `tau_agent`:

```text
src/tau_agent/types.py      JSON-like shared type aliases
src/tau_agent/messages.py   transcript message models
src/tau_agent/tools.py      tool calls, tools, and tool results
src/tau_agent/events.py     event models emitted by the agent layer
```

These modules are provider-neutral and UI-neutral. They do not mention Anthropic, OpenAI, Rich, Textual, Typer, local files, or terminal rendering.

## Why messages exist

Messages represent the transcript: the conversation state that the agent and model work with.

Tau currently defines:

- `UserMessage`
- `AssistantMessage`
- `ToolResultMessage`
- `AgentMessage`

### `UserMessage`

A `UserMessage` stores text written by the user.

```python
UserMessage(content="Read README.md")
```

Later, the harness will append one of these whenever a user submits a prompt.

### `AssistantMessage`

An `AssistantMessage` stores assistant text and optional tool calls.

```python
AssistantMessage(
    content="I'll inspect the README.",
    tool_calls=[...],
)
```

This is important because assistants can do two things in one turn:

1. produce visible text
2. request tool execution

The future agent loop will collect streamed model output into an `AssistantMessage`.
If the message has tool calls, the loop will execute those tools and continue.

### `ToolResultMessage`

A `ToolResultMessage` stores the result of a specific tool call.

```python
ToolResultMessage(
    tool_call_id="call-1",
    name="read",
    content="file contents...",
    ok=True,
)
```

The model needs tool results in the transcript so it can continue reasoning after a tool runs. For example:

```text
User asks to read README.md
Assistant calls read
Tau executes read
Tau appends ToolResultMessage
Assistant sees file contents and answers
```

### `AgentMessage`

`AgentMessage` is the union of all message types. It lets later APIs accept a transcript as:

```python
list[AgentMessage]
```

without caring whether each item is from the user, assistant, or a tool.

## Why tool types exist

Tools are how the assistant asks Tau to interact with the environment.

Tau currently defines:

- `ToolCall`
- `AgentTool`
- `AgentToolResult`

### `ToolCall`

A `ToolCall` is not the tool itself. It is a request to run a named tool with JSON-like arguments.

```python
ToolCall(
    id="call-1",
    name="read",
    arguments={"path": "README.md"},
)
```

Future provider adapters will translate provider-specific tool call payloads into this neutral Tau shape.

### `AgentTool`

An `AgentTool` describes a tool Tau can expose to a model.

It contains:

- a `name`
- a human-readable `description`
- an `input_schema`
- an async `executor`

The coding-agent layer will later register built-in tools like:

- `read`
- `write`
- `edit`
- `bash`

But the portable agent loop only needs to know that an `AgentTool` can be executed with arguments and returns an `AgentToolResult`.

### `AgentToolResult`

An `AgentToolResult` is the structured output from a tool execution.

```python
AgentToolResult(
    tool_call_id="call-1",
    name="read",
    ok=True,
    content="file contents...",
)
```

Later, the loop will convert tool results into `ToolResultMessage` objects and append them to the transcript.

## Why JSON types exist

Tool arguments and provider payloads need to be JSON-like because model APIs exchange structured data as JSON.

Tau defines shared aliases for:

- `JSONPrimitive`
- `JSONValue`
- `JSONObject`

These keep tool schemas, tool arguments, event data, and provider-neutral payloads type-safe without tying them to a specific provider SDK.

## Why events exist

Events are how the reusable agent layer reports progress without knowing who is listening.

This is the key design:

```text
Agent loop emits events
        ↓
CLI print mode consumes events
Rich renderers consume events
Textual TUI consumes events
Tests can inspect events
```

Tau currently defines events for:

- agent start/end
- turn start/end
- assistant message start/delta/end
- tool execution start/update/end
- errors

## How events will flow in the future

A future prompt run might produce this sequence:

```text
agent_start
turn_start
message_start
message_delta       "I'll inspect the file."
message_end         AssistantMessage(... tool_calls=[read])
tool_execution_start
tool_execution_end  AgentToolResult(...)
turn_end
turn_start
message_start
message_delta       "The README says..."
message_end         AssistantMessage(... no tool calls)
turn_end
agent_end
```

The important part is that the same event stream can power multiple frontends.
The core loop does not need `print()`, Rich panels, or Textual widgets.

## How Phase 1 supports later phases

### Phase 2: provider layer

Providers translate external model streams into Tau messages, tool calls, and
provider-neutral events.

### Phase 3: pure agent loop

The loop uses these types to:

1. send messages to a provider
2. collect assistant output
3. detect tool calls
4. execute `AgentTool`s
5. append `ToolResultMessage`s
6. emit `AgentEvent`s throughout

### Phase 4: harness

The harness maintains a transcript of `AgentMessage` objects and exposes
higher-level methods like `prompt()` and `continue_()`.

### Phase 6 and beyond: UI

Print mode, Rich rendering, JSON event streaming, and Textual consume
`AgentEvent`s rather than reaching into loop, provider, or harness internals.

### Phase 7: sessions

Session persistence saves and replays message objects and related state changes.
The message models give that persistence layer a stable base.

## Design rule

If a type belongs to the reusable agent brain, it lives in `tau_agent`.
If a type knows about command-line behavior, project files, slash commands, prompts, or UI rendering, it belongs outside the core agent package.
