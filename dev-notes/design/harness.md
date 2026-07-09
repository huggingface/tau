---
title: "Agent Harness"
---

`AgentHarness` is Tau's reusable stateful agent brain.

It lives in:

```text
src/tau_agent/harness.py
```

The harness owns a transcript and delegates execution to the pure agent loop.

## Basic shape

```python
harness = AgentHarness(
    AgentHarnessConfig(
        provider=provider,
        model="...",
        system="...",
        tools=[...],
    )
)

async for event in harness.prompt("Hello"):
    ...
```

## Responsibilities

The harness:

- stores transcript messages
- appends `UserMessage` objects for new prompts
- calls `run_agent_loop()`
- streams `AgentEvent` objects
- exposes `continue_()` for running without a new user prompt
- rejects overlapping `prompt()` / `continue_()` runs
- queues steering and follow-up messages for an active run
- supports event listeners
- supports basic cancellation

The harness does not know about CLI arguments, Textual, Rich rendering, slash commands, or session files.

## Queues

Use queue APIs while a run is active:

```python
harness.steer("Adjust the current plan.")
harness.follow_up("When that is done, summarize the result.")
```

Steering messages are injected after the current assistant turn and tool batch,
before the next provider call. Follow-up messages are injected only when the run
would otherwise stop. `AgentHarnessConfig.queue_mode` defaults to
`"one_at_a_time"`; set it to `"all"` to drain each queue as a full batch.

The harness emits `QueueUpdateEvent` values as queues change and as messages are
drained. Queued messages become durable transcript messages only when they are
injected into the loop.

For a detailed architecture walkthrough, read [Phase 4: AgentHarness](./architecture/phase-4-agent-harness.md).
