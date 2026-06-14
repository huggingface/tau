# Architecture Notes

This section explains Tau's architecture as it is built, one phase at a time.

Tau is intentionally developed in small layers. Each layer should answer three questions:

1. What was added?
2. Why does it exist?
3. How will later phases use it?

## Current architecture layers

```text
tau_ai       provider/model streaming layer
tau_agent    portable agent harness, loop, tools, events, sessions
tau_coding   CLI app, resources, skills, extensions, commands, UI integration
```

The most important boundary is that `tau_agent` should stay portable. It can define the reusable agent brain, but it should not know about VS Code, Textual, Rich rendering, local config directories, slash commands, or project-specific prompts.

## Phase notes

- [Phase 1: Core Types and Events](phase-1-core-types-and-events.md)

More pages will be added here as each phase lands.
