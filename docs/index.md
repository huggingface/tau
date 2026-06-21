# Tau

Tau is a Python implementation of Pi's minimalist coding-agent harness architecture.

The project is intentionally built in small, documented phases. Each phase adds one layer to the system while keeping the core agent harness independent from the coding-agent app and from any terminal UI framework.

## Architecture at a glance

```text
tau_ai       provider/model streaming layer
tau_agent    portable agent harness, loop, tools, events, sessions
tau_coding   CLI app, resources, skills, extensions, commands, UI integration
```

The key design boundary is:

```text
AgentHarness = reusable brain
AgentSession = coding-agent environment
TUI = one possible frontend
```

## Current status

Tau currently has:

- project/package foundation
- development tooling
- a `tau` console command that opens the Textual TUI by default
- non-interactive print-mode prompts
- provider-neutral message, tool, result, and event models
- a provider-neutral model streaming interface
- deterministic fake model provider for tests
- OpenAI-compatible, Anthropic, and OpenAI Codex subscription provider adapters
- provider retry events with configurable retry/backoff behavior
- provider-neutral thinking/reasoning delta events
- a pure agent loop that streams events, executes tools, drains queued prompts,
  and grows the transcript
- a reusable `AgentHarness` that owns transcript state, cancellation, and
  steering/follow-up queues
- append-only home-directory sessions
- skills, prompt templates, and project instruction discovery
- slash commands with TUI autocomplete
- provider setup and switching
- stored Tau credentials with environment-variable fallback
- context accounting, manual/automatic compaction, and HTML session export
- opt-in redacted LLM request and response observation logs
- a Textual TUI with responsive sidebar, text selection, selected-message copy,
  activity status, thinking controls, optional thinking-token display, and
  queued steering/follow-up prompts while the agent is running
- beginner-friendly design documentation

## Where to start

- New to the project? Read [Getting Started](getting-started.md).
- Installing Tau as a command? Read [Installation](installation.md).
- Looking for file formats and paths? Read [Configuration and Files](configuration.md).
- Want the full plan? Read the [Roadmap](00-roadmap.md).
- Want the big-picture boundaries? Read [Architecture](01-architecture.md).
- Want the current core model? Read [Core Types and Events](05-core-types-and-events.md).
- Want to configure model backends? Read [Providers](providers.md).
- Want to understand the execution engine? Read [Agent Loop](agent-loop.md).
- Want the reusable stateful brain? Read [Agent Harness](harness.md).
- Want to build another frontend? Read [Building a Custom TUI](custom-tui.md).
- Want a summary of the completed pre-extension hardening work? Read
  [Pre-extension Hardening Summary](architecture/pre-extension-hardening.md).
