# Dynamic provider contracts and layered registry

## What Phase 1 adds

Issue #605 implements the provider-neutral foundation from #602 Phase 1. It
does **not** add a built-in provider, startup selection, `/local`, a TUI, or any
llama.cpp behavior.

The new pieces are:

- `extensions/providers.py`: immutable provider/model/auth/transport/refresh
  contracts;
- `extensions/provider_registry.py`: process-local durable/dynamic composition
  and refresh coordination;
- `ExtensionAPI.register_provider(...)`: extension registration routed through
  the owning `ExtensionRuntime`;
- `create_dynamic_model_provider(...)`: candidate runtime construction using
  Tau's existing OpenAI-compatible transport or an extension factory;
- an explicit OpenAI transport switch that prevents dynamic model names from
  selecting `/responses` heuristically.

These APIs remain provisional until #602 Phase 6 validates them with a second
backend.

## Why dynamic providers are not durable settings

Tau's existing `ProviderConfig` objects describe user/application configuration
loaded from the bundled catalog, `~/.tau/catalog.toml`, and
`~/.tau/providers.json`. An extension registration has different ownership:
its code source and trust decision exist only in one staged runtime generation.
Persisting that definition would let a later process use provider behavior after
the source disappeared or before it was trusted.

The registry therefore receives complete durable `ProviderConfig` objects as an
immutable baseline and never serializes dynamic data. Each dynamic layer stores:

```text
provider id + source id + runtime generation id + layer id
```

Latest active registration wins. Registering the same provider from the same
source atomically replaces that source's old layer. Removing it reveals the
previous complete dynamic layer; removing the final layer returns the exact
original durable object, including headers, model metadata, compatibility,
timeouts, retries, thinking configuration, and every other field.

There is intentionally no generic snapshot disk store in this phase. A future
trusted built-in may own a versioned, allowlisted safe cache, but public extension
storage needs a separate source-identity and trust design.

## Provider and model validation

A `DynamicProvider` has a non-empty exact ID/display name, zero or more unique
`ProviderModel` IDs, an optional default that must belong to the current complete
snapshot, and exactly one runtime mechanism:

1. `OpenAICompatibleTransport`, or
2. a custom runtime factory.

Zero models is valid: it represents a dormant provider without inventing a fake
model. Unknown model metadata remains `None`; empty tuples mean “known none.”
Compatibility data is copied and JSON-validated. Runtime headers are copied but
excluded from representations and have no persistence helper.

Construction validates the whole candidate before registry mutation. An invalid
same-source replacement or refresh therefore leaves the prior layer/snapshot
unchanged.

## Authentication and secret handling

Dynamic auth is resolved only before refresh or candidate runtime creation:

```text
RequiredApiKey / OptionalApiKey
  → Tau credential reader
  → configured environment variable
  → missing result

NoAuth
  → consult neither source
```

Required auth fails with setup guidance. Missing optional auth and `NoAuth`
produce `omit_authorization_header=True`; no fake `Bearer local` value is used.
Resolved keys and auth headers are runtime-only fields excluded from `repr`.
Transport/model headers are excluded too. Static transport/model headers cannot
supply `Authorization`; bearer or custom authorization must come from the auth
strategy at resolution time. This prevents credentials from becoming part of a
registered definition while still allowing non-Bearer schemes through resolved
auth headers. Refresh diagnostics never include an extension exception string
because arbitrary errors may contain request data or a secret; diagnostics
report only a bounded category and source token.

## Refresh lifecycle

A refresh callback receives:

- a cancellation token;
- whether network use is allowed;
- the current safe in-memory model tuple;
- already resolved auth.

It returns one complete `ProviderModelSnapshot`. The coordinator:

1. identifies the exact effective source/layer/generation token;
2. shares one task among concurrent callers for that token;
3. runs discovery under a named timeout;
4. validates the whole returned snapshot;
5. rechecks source, layer, and generation synchronously;
6. publishes only when all ownership still matches.

Timeout, explicit cancellation, source replacement, unregister, reload, and
runtime retirement signal and cancel owned work. Caller cancellation is shielded
so one waiter cannot destroy work shared by another waiter. A callback that
finishes after cancellation cannot publish because publication requires the old
token still to be current. Failed work retains the current snapshot and records
at most one diagnostic per layer token; the registry also applies a global bound.

## Runtime creation and transport routing

OpenAI-compatible dynamic providers reuse `tau_ai.OpenAICompatibleProvider`.
Transport headers, model headers, and resolved auth headers are merged only in
memory. Missing optional auth passes an empty internal key plus
`omit_authorization_header=True`, so the HTTP adapter sends no Authorization
header.

The existing adapter historically inferred `/responses` for some OpenAI/Codex-
shaped model IDs. That remains the compatibility default for durable providers.
Dynamic transport descriptors explicitly own their API choice and set
`infer_api_from_model=False`; a local model named `gpt-5.4-local` or
`my-codex-local` therefore still reaches the configured `/chat/completions`
endpoint when `api="openai-completions"`.

## ExtensionRuntime ownership

Every `ExtensionRuntime` now creates one provider registry with the same explicit
generation identity as its `ExtensionGeneration`. `register_provider` stamps the
calling extension name as source ownership.

If `setup()` raises, the runtime removes all provider layers from that extension
alongside its tools, commands, guidelines, and renderers. Reload and retirement
cancel registry work and remove layers before invalidating the outgoing API.
Final `CodingSession.aclose()` also awaits the runtime registry's cooperative
refresh cancellation before closing model providers; repeated close remains a
no-op. Reload then creates a fresh generation and empty registry over the same
immutable durable baseline. Provider refresh diagnostics are projected into
normal runtime resource diagnostics without exposing secrets.

## How to verify

Focused tests cover dormant/invalid contracts, auth precedence and omission,
secret-safe representations, exact durable restoration, multi-source precedence,
same-source replacement, setup cleanup, refresh coalescing, timeout,
cancellation, malformed output, stale work, retirement, HTTP auth headers, and
model-name routing:

```bash
uv run pytest tests/test_extension_providers.py tests/test_extensions.py \
  tests/test_provider_config.py tests/test_provider_runtime.py tests/test_http.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
