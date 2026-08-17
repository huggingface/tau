---
title: Design principles
description: The handful of rules that keep Tau small, portable, and readable.
---

Tau follows a few principles consistently. They're why the codebase stays
approachable as it grows.

## Small layers beat magic

Each package has one job: `tau_ai` streams models, `tau_agent` runs the loop,
`tau_coding` is the application. You can read and test any layer on its own
without understanding the others. → [Architecture]({{< relref "./architecture.md" >}})

## Events are the contract

The agent communicates progress through a stream of provider-neutral events.
Frontends render from those events, never from provider-specific chunks or
internal control flow. This is what lets print mode, the TUI, and custom
frontends share one core. → [The agent loop & events]({{< relref "./agent-loop.md" >}})

## The core stays portable

`tau_agent` must not depend on Textual, Rich, the CLI, config directories, slash
commands, or app-specific resources. Those live in `tau_coding` and wrap the
core from outside. The reusable brain never reaches up into a UI.

## Tools are ordinary typed functions

A tool is a name, a description, a JSON input schema, and an async executor that
returns a structured result. There's no framework magic — which makes tools easy
to read, test, and add. → [Built-in tools]({{< relref "../reference/tools.md" >}})

## Sessions are durable and inspectable

Every conversation is an append-only JSONL transcript on disk. History is a tree
you can resume and branch; compaction changes the *active* context without
rewriting the record. The format is plain enough to read by hand.
→ [Sessions]({{< relref "../guides/sessions.md" >}})

## Small product divergences are explicit

Tau mostly follows [Pi](https://pi.dev)'s minimalist separation of agent brain,
coding session, and frontend. A few user-facing conveniences intentionally
diverge from that baseline. One example is automatic session naming: Tau asks the active
provider/model for a short title after the first user message is persisted, then
stores that title as session metadata. This remains in `tau_coding`, not the
portable `tau_agent` harness, because it is application workflow rather than
agent-loop behavior.

## Documentation follows implementation

Tau was built in small, documented phases so a reader can trace how the system
grew. Those phase notes live in the repo under `dev-notes/` (see
[Contributing]({{< relref "../contributing.md" >}})); these pages distill the result.
