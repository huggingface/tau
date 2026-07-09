---
title: "ADR 0001 — Use Textual for the interactive TUI"
---

## Status

Accepted for future phases.

## Context

Tau needs an interactive terminal UI, but the reusable agent harness must stay independent of frontend frameworks.

## Decision

Use Textual for the full interactive TUI after the core loop, tools, sessions, and print-mode CLI are stable.
Textual will live behind an adapter boundary that consumes agent events.

```text
AgentHarness emits events
        ↓
UI adapter consumes events
        ↓
Textual app renders chat, tool, and status components
```

## Consequences

- `tau_agent` must not depend on Textual.
- Early phases will use simple print mode and Rich renderers first.
- Textual widgets can evolve without changing the core agent loop.
