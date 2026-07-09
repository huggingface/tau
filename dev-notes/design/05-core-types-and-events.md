---
title: "05 — Core Types and Events"
---

Phase 1 defines the provider-neutral objects that later Tau phases share.

## Why these types exist

The provider layer, agent loop, harness, tools, sessions, and UI should not exchange provider-specific objects.
Instead, they use Tau's own message, tool, result, and event models.

## Messages

Messages live in `tau_agent.messages`:

- `UserMessage` records user input.
- `AssistantMessage` records assistant text and optional tool calls.
- `ToolResultMessage` records the result of a specific tool call.
- `AgentMessage` is the union of all transcript message types.

These are the objects that will eventually be passed to model providers and persisted in sessions.

## Tools

Tools live in `tau_agent.tools`:

- `ToolCall` is the assistant's request to execute a named tool with JSON-like arguments.
- `AgentTool` describes an executable tool: name, description, input schema, and async executor.
- `AgentToolResult` is the structured response from running a tool.

The core types do not implement coding tools. Built-in tools such as `read`,
`write`, `edit`, and `bash` live under the coding-agent application layer in
`tau_coding`.

## Events

Events live in `tau_agent.events`. They describe progress from the portable agent layer:

- `agent_start`
- `agent_end`
- `turn_start`
- `turn_end`
- `queue_update`
- `retry`
- `message_start`
- `message_delta`
- `thinking_delta`
- `message_end`
- `tool_execution_start`
- `tool_execution_update`
- `tool_execution_end`
- `error`

Print mode, Rich renderers, JSON event streaming, and the Textual TUI all
consume the same event stream.

`queue_update` reports pending steering/follow-up prompts. `retry` reports
provider retry progress. `thinking_delta` carries optional streamed reasoning
text from providers that expose it; frontends decide whether to show it, and it
is not recorded as durable assistant message text.

## Design boundary

These models are intentionally small and provider-neutral. Provider adapters
translate Anthropic, OpenAI-compatible, OpenAI Codex subscription, or other API
payloads into Tau types before the agent loop or frontends see them.
