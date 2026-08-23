# Tau providers and models

A provider hosts models; a model is the exact ID accepted by that provider. Use
`/login` for durable built-in credentials and `/model` to choose an available
model.

## Built-in llama.cpp

Tau includes a trusted, hidden `llama.cpp` provider layer for local inference.
Configure it in the TUI with `/local`, not `/login custom`:

```text
/local
```

The backend is recommended and preselected, but the selection is explicit. It
accepts a server URL with or without `/v1`, discovers real IDs from `/v1/models`,
and supports optional, environment, or absent authentication. No fake key or
fake model value is used. See `local-inference.md` for server setup, cache,
troubleshooting, Doctor, and reset behavior.

After setup, choose models in `/model` or start explicitly:

```bash
tau --provider llama.cpp --model <server-reported-id>
tau --provider llama.cpp --model <server-reported-id> --print "summarize this project"
```

The built-in provider is dynamic and process-local. Its safe endpoint/model
snapshot is stored under `~/.tau/state/extensions/llama.cpp.json`; its optional
secret is stored separately in `~/.tau/credentials.json`. Dynamic definitions
are never copied into `catalog.toml`, `providers.json`, or session files. A
cached snapshot can keep an explicit startup usable during temporary server
downtime. An unavailable local backend is never an implicit fallback for a new
ordinary session.

## Durable and dynamic providers

Catalog providers are durable user/application configuration. Extension provider
layers are source- and generation-owned overlays held only by an active
`ExtensionRuntime`. Their definitions and refresh snapshots are not persisted as
durable provider configuration. Removing a dynamic layer restores the complete
preceding layer or the durable baseline.

Dynamic providers support required, optional, or absent authentication without
fake keys. Secrets resolve immediately before refresh or runtime creation and are
excluded from representations, diagnostics, snapshots, sessions, and exports.
The contract is validated by a permanent second fake backend and a test-only
Ollama adapter; Ollama is not a shipped Tau backend.

Trusted built-in llama.cpp models may persist as scoped references containing
only provider ID plus exact model ID. Loaded/sleeping references are selectable.
An unloaded or missing reference remains visible as unavailable and inert: it
cannot create dynamic metadata, perform network discovery, or trigger a router
mutation. User/project dynamic providers cannot persist scoped references.

## Existing custom providers

Other OpenAI-compatible endpoints can still be configured with `/login custom`,
`tau setup`, or a user-level `~/.tau/catalog.toml` entry. That path is useful for
Ollama, gateways, and older manually configured local providers. An existing
provider named `llama-cpp` remains distinct from the built-in `llama.cpp`; Tau
does not migrate or overwrite it.

## Metadata and selection rules

Use exact provider/model IDs. Tau does not infer context windows, output limits,
reasoning support, modalities, pricing, or tool compatibility when a server does
not report them. Local Doctor can verify streaming and tool-call behavior only
when explicitly requested. If a refreshed server no longer reports the active
model, Tau keeps the current runtime usable and marks the model snapshot stale;
it does not silently replace the model.

Provider/model selection precedence is explicit CLI selection, the resumed
transcript's provider-aware model entry, the session record for legacy sessions,
a durable default, and then a usable durable provider. Dynamic local providers
are selected only by explicit startup, a resumed local session, or an in-session
`/model`/`/local` action. The old `llama-cpp` catalog provider is not migrated;
configure the built-in `llama.cpp` layer separately through `/local`.
