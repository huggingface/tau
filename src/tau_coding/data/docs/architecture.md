# Tau architecture

Tau preserves Pi's separation of concerns:

```text
AgentHarness = reusable agent brain
AgentSession = coding-agent environment
TUI = one possible frontend
```

Packages:

- `tau_ai`: provider/model streaming and provider-neutral events.
- `tau_agent`: portable harness, loop, tools, messages, events, and sessions.
- `tau_coding`: CLI application, resources, skills, extensions, commands, persistence, rendering, and TUI integration.

Keep `tau_agent` independent of Typer, Rich, Textual, application resource locations, and provider-specific assumptions. Prefer typed data models, explicit async boundaries, deterministic fakes, and small abstractions.

Dynamic provider contracts and composition belong to `tau_coding.extensions`.
Every staged `ExtensionRuntime` owns a fresh source/generation/layer-aware
`DynamicProviderRegistry`; durable `ProviderConfig` objects are immutable baseline
inputs. Dynamic source identity is host-owned and canonical-entry-path-based, not an
extension display name. The registry is frontend-free and process-local. Retirement
atomically invalidates dynamic layers and removes cancelled operations from
coalescing. Discovery receives one task cancellation; async close waits at most
0.25 seconds from that request without cancelling `finally` cleanup again. A task
still running is explicitly contained, not drained, and a process-owned supervisor
keeps its task and registry reachable until completion under stale-publication guards.
Reload, session replacement,
and final close await this drain/containment step. OpenAI-compatible dynamic
runtimes reuse `tau_ai.OpenAICompatibleProvider` through an explicit transport
choice, so model names cannot silently select another endpoint API.

In a Tau checkout, read `AGENTS.md`, `website/content/internals/architecture.md`, and relevant `dev-notes/architecture/` documents before broad architectural changes.
