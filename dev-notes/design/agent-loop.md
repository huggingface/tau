---
title: "Agent Loop"
---

Tau's pure agent loop is implemented by `run_agent_loop()` in `tau_agent.loop`.

It connects:

```text
transcript + tools + provider
```

and emits agent events while appending new messages to the transcript.

## Minimal shape

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

The loop is intentionally independent of the CLI, Rich, Textual, and session file locations.

The current loop also accepts optional queue-drain hooks from `AgentHarness`.
Those hooks let the loop inject steering messages after a turn/tool batch and
follow-up messages when a run would otherwise stop, while still emitting normal
user message events and mutating the same transcript list.

For a detailed architecture walkthrough, read [Phase 3: Pure Agent Loop](./architecture/phase-3-agent-loop.md).
