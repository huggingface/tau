---
title: "00 — Roadmap"
---

Tau is being built as a Python implementation of Pi's minimalist coding-agent harness architecture.
The goal is not a line-by-line port; the goal is to preserve the same boundaries while using Python-native tools.

## Package layers

```text
tau_ai       provider/model streaming layer
tau_agent    portable agent harness, loop, tools, events, sessions
tau_coding   CLI app, resources, skills, extensions, commands, UI integration
```

## Current status

Phases 0 through 20.4, 22, and 23 are implemented and documented. Phase 21
extensions are intentionally deferred so the core harness, coding session, and
TUI can stay stable before extension loading is introduced.

The latest pre-extension hardening pass added context accounting refreshes,
thinking-mode controls, optional thinking-token display, provider retries,
credential precedence, skill invocation reliability, session export, transcript
selection/copy, activity status, and Pi-style queued steering/follow-up prompts.
See [Pre-extension Hardening Summary](./architecture/pre-extension-hardening.md)
for the current behavior and verification coverage.

Context compaction now uses Pi-style model-generated summaries, preserves recent
context during automatic compaction, and can recover from a context-overflow
provider error with one compact-and-retry attempt. See
[Context Compaction](./context-compaction.md).

## Phase plan

0. Project foundation and design docs.
1. Core message, tool, and event types.
2. Provider interface with fake and real providers.
3. Pure agent loop.
4. Reusable `AgentHarness`.
5. Built-in coding tools.
6. Non-interactive print-mode CLI.
7. Append-only session tree persistence.
8. Coding session wrapper with commands.
9. Skills and prompt templates.
10. System prompt assembly.
11. Print and event rendering modes.
12. Textual TUI behind an adapter boundary.
13. Tau home, paths, and automatic `.agents` resources.
14. Session manager and resume.
15. Slash command registry.
16. Robust skills and prompt discovery.
17. TUI slash-command autocomplete.
18. Provider configuration and setup.
19. Project context discovery and reload.
20. Packaging and installation polish.
20.1. Accurate context accounting and sidebar refresh.
20.2. Thinking mode controls.
20.3. Skill invocation reliability.
20.4. Session visualization and export.
21. Extensions. Deferred.
22. Compaction and context management.
23. Advanced TUI and product polish.

## Phase 0 deliverables

Phase 0 creates the docs, package scaffold, development checks, and a basic `tau --version` command.
