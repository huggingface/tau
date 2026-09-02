---
title: Architecture overview
description: How Tau is split into three layers — and why that boundary is the whole point.
---

Tau is deliberately small and layered. The most important design idea is a
**boundary**: the reusable agent "brain" knows nothing about terminals, file
paths, or rendering. Everything app-specific wraps around it.

## Three packages

```text
tau_coding  →  tau_agent  →  tau_ai
```

### `tau_ai` — talking to models

Owns provider-specific model streaming. It translates each provider's API
(OpenAI, Anthropic, …) into Tau's **provider-neutral event stream**, so nothing
above it has to care which model vendor is in use.

### `tau_agent` — the portable brain

Owns the reusable agent core: messages, tools, events, the
[agent loop]({{< relref "./agent-loop.md" >}}), the harness, and session primitives. This package
must **not** import CLI, Rich, Textual, or resource-loading code. That's what
keeps it portable.

### `tau_coding` — the coding application

Owns everything that makes Tau a *coding agent you run*: the CLI, the built-in
[tools]({{< relref "../reference/tools.md" >}}), [project instructions]({{< relref "../guides/project-instructions.md" >}}),
[skills and prompts]({{< relref "../guides/skills-and-prompts.md" >}}),
[sessions on disk]({{< relref "../guides/sessions.md" >}}), provider configuration, and the
Textual TUI.

### Local-backend boundary

Local inference belongs to `tau_coding`, not the portable agent core. A staged
extension runtime owns a source- and generation-aware local-backend registry
beside its provider registry. Backends return typed configuration, status,
progress, diagnostics, and capability values; the Textual adapter renders them
and owns cancellation, confirmation, and idle checks. Pairing each backend with
its exact source-owned provider layer prevents a shadowing extension from using
or resetting another source's integration. See the [local backends
guide]({{< relref "../guides/local-inference.md" >}}).

Phase 6 validates the boundary with a permanent second fake backend and a
test-only Ollama adapter. Provider discovery and backend status may use
separate endpoints, installed/running state fits generic model state, and
`NoAuth` needs no special-case backend API. Refresh cancellation is bounded and
generation-safe; late work cannot publish after retirement. Router mutations
remain a later phase.

TUI and print startup share the same trust-aware preparation boundary: load the
trusted built-ins and eligible extensions, restore safe dynamic snapshots,
resolve an explicit provider/model, then construct the candidate runtime. A
saved llama.cpp snapshot can therefore support explicit startup during server
downtime without making the local backend an implicit fallback.

## Dependency direction

Dependencies only point one way: `tau_coding → tau_agent → tau_ai`. UI code
*consumes* events; the core never reaches up to render anything. In one line:

```text
AgentHarness = reusable brain
CodingSession = coding-agent environment
TUI = one possible frontend
```

## Why the boundary matters

Because the core is UI-free, the same agent can drive print mode, the Textual
TUI, or a frontend you build yourself — all by consuming the same event stream.
That's also what makes Tau readable: each layer answers one question, and you can
study it without untangling the others.

→ Next: [The agent loop & events]({{< relref "./agent-loop.md" >}}) ·
[Design principles]({{< relref "./design-principles.md" >}}) ·
[Build your own frontend]({{< relref "./custom-frontend.md" >}})

{{% note title="Going deeper" %}}
The phase-by-phase build journals, design docs, and ADRs live in the repo under
`dev-notes/` (not on this site). See [Contributing]({{< relref "../contributing.md" >}}).
{{% /note %}}
