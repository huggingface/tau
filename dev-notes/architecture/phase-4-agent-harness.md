---
title: "Phase 4: AgentHarness"
---

Phase 4 adds `AgentHarness`, the reusable stateful brain built on top of the pure agent loop.

The previous phase introduced `run_agent_loop()`, which coordinates a provider, tools, messages, and events. That loop is intentionally mostly stateless: callers pass in a transcript list, and the loop appends assistant and tool result messages.

`AgentHarness` owns that transcript for callers.

## What was added

Phase 4 added:

```text
src/tau_agent/harness.py
```

with:

- `AgentHarnessConfig`
- `AgentHarness`
- `SimpleCancellationToken`
- `EventListener`

## Why the harness exists

Tau's architecture separates three layers:

```text
AgentHarness = reusable brain
AgentSession = coding-agent environment
TUI = one possible frontend
```

The harness is the reusable stateful layer. It is still not the full coding-agent app.

That means it can own conversation state and run the loop, but it should not know about:

- where session files are stored
- how slash commands work
- how project instructions are discovered
- how Rich or Textual render events
- which built-in coding tools are registered by default

Those responsibilities belong to later `tau_coding` phases.

## Harness configuration

`AgentHarnessConfig` stores the stable inputs needed to run the loop:

- provider
- model name
- system prompt
- tools
- optional max turn count

Conceptually:

```python
config = AgentHarnessConfig(
    provider=provider,
    model="gpt-4.1-mini",
    system="You are Tau.",
    tools=[...],
)
```

The harness receives this config and uses it for every prompt or continuation.

## Prompt flow

Calling `prompt()` appends a user message and starts the loop.

```python
async for event in harness.prompt("Read README.md"):
    ...
```

The transcript changes like this:

```text
before prompt:
  []

after prompt appends user message:
  UserMessage("Read README.md")

after loop runs:
  UserMessage("Read README.md")
  AssistantMessage(...)
  ToolResultMessage(...)   # if tools were used
  AssistantMessage(...)
```

The harness does not render those events. It simply yields them.

## Continue flow

Calling `continue_()` runs the loop without appending a new user message.

This is useful for future features such as:

- resuming after restoring a session
- continuing after an interrupted run
- letting a coding-session wrapper decide when the next model turn should happen

```python
async for event in harness.continue_():
    ...
```

## Transcript snapshots

The `messages` property returns an immutable tuple snapshot.

This protects the harness transcript from accidental external mutation:

```python
snapshot = harness.messages
```

A future session layer can use this snapshot for persistence. For controlled restoration, `append_message()` can append existing messages.

## Event listeners

The harness supports event subscriptions:

```python
unsubscribe = harness.subscribe(listener)
```

Listeners receive the same `AgentEvent` objects yielded by `prompt()` and `continue_()`.

This lets future code observe runs without becoming the main event consumer. For example:

- logging
- metrics
- session persistence hooks
- UI bridges

The primary API remains async iteration over events.

## Cancellation

Phase 4 adds a minimal cancellation token.

```python
harness.cancel()
```

When cancellation is requested, the current loop can stop and emit a recoverable cancellation error.

This is intentionally simple. Later UI layers can connect it to keybindings, buttons, or command handlers.

## Relationship to the loop

The harness delegates to `run_agent_loop()`:

```text
AgentHarness.prompt()
        ↓
append UserMessage
        ↓
run_agent_loop(...)
        ↓
yield AgentEvent objects
        ↓
transcript grows
```

The loop still owns the turn-by-turn mechanics. The harness owns durable in-memory conversation state.

## How Phase 4 supports later phases

### Phase 5: built-in coding tools

The harness already accepts `AgentTool` instances. Once coding tools exist, the coding app can pass them into the harness config.

### Phase 6: print-mode CLI

The CLI can create an `AgentHarness`, call `prompt()`, and print streamed events.

### Phase 7: sessions

Session persistence can restore messages into a harness and save snapshots after events.

### Phase 8: coding session wrapper

The coding session wrapper can compose higher-level behavior around the harness: slash commands, direct bash commands, prompt expansion, resources, and persistence.

## Design rule

`AgentHarness` can own agent state, but it must remain frontend-agnostic and coding-app-agnostic.

If a behavior depends on terminal rendering, project files, user config directories, or slash commands, it belongs outside `tau_agent`.
