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

Phase 6 validates these APIs with a permanent second fake backend and a
small test-only Ollama adapter. The adapter uses separate provider and local
status endpoints without adding provider-specific concepts to the contract.

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

Latest active registration wins. For loaded extensions the host derives a stable
source ID from the canonical entry-file path; the extension display name is never
used as provider ownership. The loader freezes every discovered ID before importing
any extension and stores it on `LoadedExtension`. Import/setup code can therefore
retarget the entry symlink or an ancestor without changing duplicate detection, API
registration, or failed-setup cleanup ownership. Separate paths named `shared.py`
receive different layers even when loaded in separate trust stages. Repeating the
same canonical entry within one runtime is ignored with first-loaded precedence; a
fresh runtime generation can load that stable source again. Registering the same
provider from the same source atomically replaces that source's old layer. Removing
it reveals the previous complete dynamic layer; removing the final layer returns the
exact original
durable object, including headers, model metadata, compatibility, timeouts, retries,
thinking configuration, and every other field. Tools and commands remain separate,
first-registration-wins name registries.

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
Compatibility data is copied, JSON-validated, and deeply frozen: nested objects
become read-only mappings and arrays become tuples. Registry views, refresh results,
and callback `cached_models` can therefore share one validated model value without
letting consumers mutate active state. Runtime construction converts compatibility
data back to fresh ordinary JSON dictionaries/lists at the transport boundary.
Runtime headers are copied but excluded from representations and have no persistence
helper.

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
Resolved keys, auth headers, and extension-provided auth provenance are runtime-only
fields excluded from `repr`. Transport/model headers are excluded too. Static
transport/model headers cannot supply `Authorization`; bearer or custom authorization
must come from the auth strategy at resolution time. This prevents credentials from
becoming part of a registered definition while still allowing non-Bearer schemes
through resolved auth headers. Refresh diagnostics never include an extension
exception string or resolved auth data because either may contain request data or a
secret; diagnostics report only a bounded category and source token. Runtime creation
likewise converts custom auth-resolution exceptions to a categorical host error.
Only Tau's exact `RequiredApiKey` strategy preserves its host-authored missing-key
setup guidance.

## Refresh lifecycle

A refresh callback receives:

- a cancellation token;
- whether network use is allowed;
- the current safe in-memory model tuple;
- already resolved auth.

It returns one complete `ProviderModelSnapshot`. The coordinator:

1. identifies the exact effective source/layer/generation token;
2. shares one task only among callers whose token and `allow_network` policy match;
3. applies each caller's own timeout while shielding compatible shared work;
4. validates the whole returned snapshot;
5. rechecks source, layer, generation, and operation revision synchronously;
6. publishes only when all ownership still matches.

Opposite network policies use separate discovery tasks, so a no-network caller never
consumes network-enabled discovery and a later network-enabled caller can still do
network work. A short waiter can time out without ending work for a compatible longer
waiter. When the final waiter times out—or `cancel_refresh` cancels an operation—the
coalescing entry is synchronously detached before that caller returns. An immediate
retry therefore creates a new operation. Each done callback carries the old operation
identity, so it cannot remove a successor created under the same key. Detached work
remains in a separate owned-operation set until actual completion.

Timeout, explicit cancellation, source replacement, unregister, reload, and runtime
retirement signal and cancel owned work. Caller cancellation is shielded so one
waiter cannot destroy work shared by another waiter. Discovery receives at most one
`Task.cancel()` request. Async close waits until the callback exits or until 0.25
seconds have elapsed from that request; it never injects a second cancellation into
an ordinary `finally` block merely because cleanup exceeded 0.1 seconds. Cleanup
that completes within the containment interval is drained before reload,
replacement, reset close, or final close returns.

A callback still running at 0.25 seconds is classified as **contained**, not drained.
`ProviderRegistryCloseResult(drained=False, contained_discovery_tasks=...)` reports
that exact state. A process-owned supervisor strongly retains the discovery task, whose done callback
retains its retired generation registry, until actual completion. This explicit root
avoids relying on asyncio's weak task references after a session drops the outgoing
runtime. The callback has no publication path after the outer operation ends, and
source/layer/generation/revision guards remain in force.
Failed work retains the current snapshot and records at most one diagnostic per
layer token; the registry also applies a global bound.

## Runtime creation and transport routing

OpenAI-compatible dynamic providers reuse `tau_ai.OpenAICompatibleProvider`.
Transport headers, model headers, and resolved auth headers are merged only in
memory. Missing optional auth passes an empty internal key plus
`omit_authorization_header=True`, so the HTTP adapter sends no Authorization
header.

Custom runtime factories are accepted only when both `stream_response` and `aclose`
are callable. A constructed candidate rejected by validation is closed exactly once
when possible; close errors are contained so they cannot replace the categorical
configuration error.

The existing adapter historically inferred `/responses` for some OpenAI/Codex-
shaped model IDs. That remains the compatibility default for durable providers.
Dynamic transport descriptors explicitly own their API choice and set
`infer_api_from_model=False`; a local model named `gpt-5.4-local` or
`my-codex-local` therefore still reaches the configured `/chat/completions`
endpoint when `api="openai-completions"`.

## ExtensionRuntime ownership

Every `ExtensionRuntime` now creates one provider registry with the same explicit
generation identity as its `ExtensionGeneration`. The loader assigns each discovered
entry a source ID from `Path.resolve().as_uri()` before it imports any extension,
stores that value on
`LoadedExtension`, and uses only the stored ID afterward. `register_provider` uses
that host-owned ID. Display names remain user-facing labels only.

If `setup()` raises, the runtime removes only registrations carrying that exact
stored source ID, including providers, tools, commands, guidelines, renderers, and
event handlers. A failed second `shared.py` therefore cannot remove a successful
first `shared.py` from another path; successful same-name providers shadow and
restore as normal independent layers. Duplicate tools and commands are still ignored
by name, so their first-registration semantics are unchanged.

Reload and retirement cancel registry work and remove layers before invalidating the
outgoing API. `CodingSession.reload()` and destination replacement synchronously
publish the fresh state, then place outgoing runtime close in an independently owned
task. Cancellation at that committed seam is contained until cleanup drains or
reports bounded hostile work, and the operation returns the adopted result rather
than claiming rollback. Final `CodingSession.aclose()` similarly owns one durable
close task, but propagates observed caller cancellation only after the extension
registry and every provider ledger entry have received their one close attempt.
Repeated close observes the same task and cannot close a provider twice. A staged
replacement explicitly owns its candidate provider ledger through outgoing shutdown,
incoming start, and every other pre-publication seam. Cancellation or failure closes
that candidate session before propagating while leaving the active provider open;
success transfers the ledger once to the surviving outer session. `reset_for_reload()`
is synchronous, so it retains each retired registry for the
runtime's later async close. A contained task stays process-supervised with its
retired registry through its done callback; it is not mislabeled as drained. Reload
then creates a fresh generation and empty registry over the same immutable durable
baseline. Provider refresh diagnostics are projected into normal runtime diagnostics
without exposing secrets.

## How to verify

Focused tests cover dormant/invalid contracts, auth precedence and omission,
secret-safe representations, exact durable restoration, multi-source precedence,
same-source replacement, same-name/different-path and symlink-retarget isolation,
complete failed-setup cleanup, policy-safe and immediate-retry-safe refresh
coalescing, per-waiter timeouts, single-cancel cooperative cleanup, explicit
hostile-work containment, pre-publication candidate aborts, cancellation-at-publication
lifecycle draining, final close/provider-ledger discharge, malformed output, stale work,
deep immutability, rejected runtime cleanup, auth-exception redaction, no durable writes,
retirement, HTTP auth headers,
and model-name routing:

```bash
uv run pytest tests/test_extension_providers.py tests/test_extensions.py \
  tests/test_provider_config.py tests/test_provider_runtime.py tests/test_http.py
uv run ruff check .
uv run ruff format --check .
uv run mypy
```
