---
title: "Pi-aligned dynamic extension providers"
---

# Pi-aligned dynamic extension providers

## What changed

Tau extensions now register a `DynamicProvider` object that owns its transport,
authentication strategy, structured model snapshot, optional async discovery,
and optional future runtime factory. The initial concrete helper is
`OpenAICompatibleTransport`, which reuses `tau_ai` rather than adding a new
streaming implementation.

Public pieces:

- `DynamicProvider(id, display_name, transport, auth, models, ...)`
- `ProviderModel` with optional display/capability/limit metadata
- `RequiredEnvApiKey`, `OptionalEnvApiKey`, and `NoAuth`
- `register_provider`, `refresh_provider_models`, `unregister_provider`
- async, current-session-only `select_model`
- extension-scoped non-secret JSON settings

A provider may have zero models. This lets an extension expose setup/status
commands before its server is configured or reachable. Unknown model limits,
modalities, and reasoning support remain unknown instead of receiving guesses.

## Why provider objects

The first prototype represented an extension provider as host configuration and
mutated its model list through a separate API. Pi's provider registration model
showed a better ownership boundary: the provider owns authentication, model
snapshots, and refresh behavior; the host owns lifecycle, selection, and UI
integration. This shape can serve llama.cpp, vLLM, Ollama, LM Studio, remote
gateways, and eventually non-OpenAI runtimes without putting any of them in
`tau_agent`.

## Layered registry

`DynamicProviderRegistry` tracks one layer per extension and stable provider ID.
A valid re-registration replaces only that extension's layer. Multiple
extensions may use the same provider ID; the latest registration layer is
effective. Removing it reveals the preceding extension layer. Durable built-in
settings form the base layer, so unregister/reload restores a built-in override
rather than deleting the provider.

Validation happens before replacement. A malformed update therefore leaves the
working layer untouched. Setup failure removes only registrations owned by the
failing extension. Reload cancels outgoing refresh work, removes the outgoing
generation, then rebuilds layers from newly imported extensions.

Registrations remain process-local. They are composed over durable provider
settings but are never sent through catalog persistence. `CodingSession` keeps
the durable base separately so it can reconstruct overlays and restore built-ins
correctly.

## Async discovery

A provider can define:

```python
async def refresh(context) -> Sequence[ProviderModel]: ...
```

The context carries a cancellation event, network policy, non-secret endpoint,
auth resolver, and source identity. The host invokes refresh only for an
explicitly requested startup provider; unrelated startup does not perform local
network discovery. Extensions can request refresh later from their own commands,
and model-picker refresh can use the same host method in a follow-up.

A successful result is validated and published as one snapshot. Failure records
an extension diagnostic and returns the last-known snapshot. Concurrent requests
for the same layer share one task. Unregister, reload, and shutdown cancel owned
refresh work.

## Startup ordering

`provider_startup.prepare_provider_startup` is shared by TUI and print mode:

1. resolve the target cwd (including an explicit resumed session)
2. load user, explicitly requested, and explicitly trusted project extensions
3. restore cached provider snapshots during synchronous `setup(tau)`
4. refresh only the explicitly targeted provider
5. compose effective overlays over durable settings
6. let the host resolve provider/model and create the coding session

This removes the duplicated extension-provider loading path that previously
lived in `cli.py` and `tui/app.py`.

## Runtime switching and cleanup

Extension selection is async. The session validates the model and creates the
candidate runtime before changing active state. If creation fails, the previous
provider/model remains intact. After a successful swap, Tau closes the replaced
Tau-owned runtime when it exposes `aclose`. Selection updates the current session
record but deliberately does not persist an extension provider as Tau's durable
default.

The OpenAI-compatible projection uses Tau's existing runtime builder. A
`runtime_factory` seam is present on `DynamicProvider` for provider-neutral
future transports; `tau_agent` continues to see only its `ModelProvider`
protocol.

## Authentication and secrets

- required env auth gives actionable missing-variable guidance
- optional env auth uses the key when present and sends no Authorization header
  when absent
- no auth always sends no Authorization header
- Tau does not synthesize `Bearer local` or require fake credentials
- extension JSON settings remain user-scoped and must contain no secrets

## Intentional differences from Pi

- llama.cpp remains an external, explicitly loaded extension rather than a
  hidden built-in extension.
- Tau does not require llama.cpp router mode, manage processes/models, invent a
  local bearer token, or guess limits.
- extension `setup()` stays synchronous; awaited work belongs in host-invoked
  refresh callbacks.
- source ownership is retained in every provider layer, making reload and
  unregister restoration explicit.
- v1 provides the OpenAI-compatible helper while retaining a factory-ready
  provider object.

## How to test

```bash
uv run pytest tests/test_extensions.py tests/test_cli.py tests/test_coding_session.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
hugo --source website --minify
```

Manual validation uses an external extension that registers cached models and an
async `/v1/models` refresh callback:

```bash
tau -e ./test-local-extension --provider local-test --model <model> --print "Say hello"
tau -e ./test-local-extension
```

Verify `/model`, `/reload`, and `/resume`; unauthenticated and env-key-protected
endpoints; failed refresh with cached models; and an override/unregister cycle
that restores the previous provider.
