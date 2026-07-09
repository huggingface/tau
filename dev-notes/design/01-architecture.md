---
title: "01 — Architecture"
---

Tau keeps the reusable agent core separate from the coding-agent application and from any UI.

## Layers

### `tau_ai`

Owns provider-specific model streaming. It will translate provider responses into Tau's provider-neutral events.

### `tau_agent`

Owns the portable agent brain: messages, tools, events, the agent loop, harness, and session primitives.
This package must not import CLI, Rich, Textual, or application resource loading code.

### `tau_coding`

Owns the coding-agent application: CLI commands, built-in coding tools, project instructions, skills,
prompt templates, sessions on disk, and UI adapters.

## Dependency direction

```text
tau_coding -> tau_agent -> tau_ai
```

UI packages consume emitted events. The core does not render UI directly.

## Guiding split

```text
AgentHarness = reusable brain
AgentSession = coding-agent environment
TUI = one possible frontend
```
