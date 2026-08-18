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
inputs. The registry is frontend-free and process-local. Retirement atomically
invalidates dynamic layers, cancels owned refresh/discovery work, and retains inner
task handles until actual completion; async close drains cooperative cleanup with a
bounded cancellation-suppression policy. OpenAI-compatible dynamic runtimes reuse
`tau_ai.OpenAICompatibleProvider` through an explicit transport choice, so model
names cannot silently select another endpoint API.

In a Tau checkout, read `AGENTS.md`, `website/content/internals/architecture.md`, and relevant `dev-notes/architecture/` documents before broad architectural changes.
