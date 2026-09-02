# ADR: session preparation and dynamic-provider lifecycle

- **Status:** accepted for issue #602 Phase 0
- **Date:** 2026-08-18
- **Scope:** architecture and current-behavior characterization only
- **Implementation:** later phases; this ADR adds no dynamic provider, built-in extension, `/local`, or llama.cpp runtime code

## Why this decision exists

Tau currently asks both frontends to choose and construct a model provider before
`CodingSession.load()`. Extensions load inside `CodingSession.load()`, after that
provider already exists. An extension therefore cannot supply a provider selected
on the command line or restored from a session.

Fixing only that ordering would be unsafe. Startup, reload, resume, new-session,
branching, trust, model switching, transcript persistence, extension generations,
and provider cleanup meet at the same boundary. This ADR defines that boundary
before production behavior changes.

Historical PRs #352 and #417 were consulted only as evidence. No code or branch
from either PR is reused. In particular, this design rejects PR #417's
process-long provider/extension registry assumption.

## Pi compatibility check

Pi was rechecked at the accepted-plan pin
`eb1f87fa9a29e27e0c63dcb40dbed9a3624c82b1` (2026-08-17). The upstream remote
HEAD observed during the audit was `2509b5c037d366979f2febfce4174b88aeaadc6a`;
the pin remains the reproducible architectural reference for this phase.

The pinned implementation confirms the patterns Tau should follow:

- extension provider registrations reach a model runtime before normal model
  use;
- extension contexts are invalidated after replacement/reload;
- `session_shutdown` precedes generation teardown and `session_start` follows
  replacement;
- interactive reload refuses to run while a response is active;
- llama.cpp protocol and management behavior live in the bundled extension.

Tau deliberately differs where its architecture requires it: fresh cwd/trust-
bound registries, `/local` rather than `/llama`, no fake auth, single-model
server support, no guessed metadata, no silent unload, and the durable
commit/no-fail publication protocol below.

## Current-main audit

The audit baseline is Tau `9c1285d68f41059105bd8a834d309a4d601a3b07` plus
the accepted #602 plan commit. Relevant code is in:

- `src/tau_coding/cli.py`
- `src/tau_coding/tui/app.py`
- `src/tau_coding/session.py`
- `src/tau_coding/project_trust.py`
- `src/tau_coding/extensions/runtime.py`
- `src/tau_coding/provider_config.py`
- `src/tau_coding/provider_runtime.py`
- `src/tau_agent/session/`

### Startup today

Print startup currently does this:

```text
load durable ProviderSettings
→ resolve record and provider/model
→ create provider
→ CodingSession.load(provider=...)
→ install stderr extension UI
→ emit pending session_start
→ run prompt
→ close CodingSession-owned replacement providers
→ frontend closes the original provider
```

TUI startup follows the same provider-first ordering. If provider construction
fails, only the TUI substitutes `LoginRequiredProvider`; print mode fails. The
TUI then creates or resolves its session record and calls `CodingSession.load()`.
Explicit TUI and print resume use the record's provider/model anchor when one is
available.

`CodingSession.load()` already has useful staging behavior:

1. Read the transcript and derive the active leaf.
2. Create a fresh, cwd-bound `ExtensionRuntime`.
3. Load user and explicit extensions, excluding project extensions.
4. Resolve project trust.
5. Build a coherent trusted or untrusted resource plan.
6. Load opted-in project extensions only after approval.
7. Compose tools, commands, guidelines, and system prompt.
8. Build and bind the harness.
9. Defer `session_start` until the host installs its UI bridge.

There is no known path in this sequence that imports project extension code
before the destination trust decision. This invariant must survive extraction.

However, `load()` is not a complete preparation transaction. It can persist tool
history repairs and discover runtime model limits. It also receives an already
constructed provider and may replace it from durable settings. Initial provider
ownership is consequently split between frontend and session.

### Replacement and reload today

Reload and destination replacement stage a fresh extension runtime. Cancellation
or preparation failure generally keeps the outgoing session. Project trust cache
publication is delayed until adoption, although a saved UI choice is currently
written to `trust.json` during resolution rather than staged with adoption.

The current publication sequence still has gaps:

- lifecycle callbacks run before a synchronous field swap but are not yet a
  formally contained no-fail region;
- destination transcript repair may write during preparation;
- transcript initialization/model/leaf writes are not one atomic batch;
- session index writes are not explicitly classified as repairable cache writes;
- replacement provider ownership is split;
- `new_session()` and `resume()` do not themselves enforce an idle harness;
  the TUI currently cancels an active run before invoking some replacements;
- branch replacement does reject an active run.

### Model selection and recovery today

`ModelChangeEntry` stores only `model`. Provider identity lives in the rebuildable
session index record. Replay therefore recovers the model on the selected branch
but cannot recover a cross-provider branch unambiguously. Current branch tests
show the consequence: state can replay an earlier model while the configured
runtime remains anchored to the record's provider/current model.

Cross-provider selection creates a candidate before mutating the active provider,
so runtime-construction failure preserves the old pair. Same-provider selection
is mutation-first. Selection writes durable defaults and index metadata, but
ordinary picker switches do not atomically append provider/model history plus a
leaf.

### Cleanup today

Providers created by a session switch are kept in `CodingSession._owned_providers`
and closed on final session close. The provider constructed by print/TUI startup
is frontend-owned and closed by the frontend. This split explains duplicate
cleanup responsibilities and must not survive the shared preparation service.

## Decision

### 1. Three explicit preparation stages

Create `src/tau_coding/session_preparation.py` in Phase 3. It owns one request and
returns a `PreparedCodingSession` with idempotent `adopt()` and `abort()`.

#### State stage

- Resolve a new or existing session record and canonical destination cwd.
- Read transcript/session state under the session lock.
- Determine the active leaf, legacy provider anchor, and staged repair/initial
  entries.
- Do not create a provider, load project content, or write authoritative JSONL.

#### Environment stage

- Create a fresh destination `ExtensionRuntime`.
- Load trusted built-ins first, then user and explicit extensions.
- Detect protected project inputs and resolve trust.
- Only after trust, load project resources and opted-in project extensions.
- Restore safe built-in snapshots and compose the effective durable/dynamic
  provider view.
- Do not perform network refresh for unrelated providers.

#### Runtime stage

- Resolve provider/model against the effective view.
- Refresh only an explicitly required dormant provider when no safe snapshot can
  satisfy the request.
- Resolve authentication immediately before constructing the candidate.
- Create provider, harness, coding session, and buffered frontend attachment.
- Return a prepared candidate without authoritative transcript/index writes.

The existing direct `CodingSession.load(provider=...)` seam remains as a
compatibility wrapper for tests and embedded static-provider consumers. CLI and
TUI must use the shared preparation service; neither may recreate this ordering.

### 2. Trust order is fixed

The environment stage preserves this order:

```text
built-in/user settings
→ built-in, user, and explicit extension setup
→ metadata-only project detection
→ override / eligible extension / saved / default / UI trust resolution
→ trusted project resources
→ opted-in trusted project extension setup
→ provider snapshot restoration and effective composition
→ optional requested-provider refresh
```

A project extension cannot approve or register itself before trust. Trust remains
an input-loading guard, not a process, filesystem, credential, network, provider,
or model sandbox.

### 3. Registries are generation-local

Every staged `ExtensionRuntime` owns exactly one dynamic-provider registry and
one local-backend registry. Registries are not process-global and are never
shared across destination cwd, reload, resume, or new-session preparation.

Each registration carries:

- stable source identity;
- extension/runtime generation identity;
- provider layer identity.

Retiring a generation cancels its tasks and removes only its registrations.
Publication from an old source/layer/generation token is rejected. Durable
provider settings are an immutable baseline input, not a dynamic registry.

Dynamic provider precedence is deliberate latest-active-layer precedence:

durable baseline, built-in, user, explicit, trusted project. Removing one layer
reveals the preceding complete definition.

### 4. Local backends bind to source-owned provider layers

A local backend is bound to the provider layer registered by the same source and
generation, not merely to a provider ID string. If another source shadows that
provider ID, the backend may show status/configuration but cannot select, reset,
or mutate the shadowing layer. Reset removes only source-owned state,
registration, snapshot, and credential reference.

This source binding is mandatory before `/local` exists.

### 5. Preparation has one owner ledger

Ownership changes only at explicit transitions:

| Resource | During preparation | After adoption | On abort |
| --- | --- | --- | --- |
| candidate provider | `PreparedCodingSession` | `CodingSession` | close once |
| extension/provider registries | `PreparedCodingSession` | `CodingSession` | retire once |
| refresh/tasks | staged generation | adopted generation | cancel and await |
| outgoing session resources | outgoing `CodingSession` | retired after publication | remain live |
| frontend bridge | buffered candidate attachment | active frontend | discard buffer |

Frontends do not close adopted providers. `CodingSession.aclose()` is the sole
final owner path and is idempotent. A failed switch closes only its candidate.
A successful switch closes the replaced Tau-owned provider after publication.
External llama.cpp server processes and model files are never Tau-owned.

### 6. Adoption and abort protocol

Preparation is reversible. Before the durable commit, any error/cancellation
calls `abort()`, closes staged resources, commits no trust decision, and leaves
the outgoing session and destination transcript unchanged.

Adoption does this:

1. Require the outgoing harness to be idle.
2. Attach the candidate frontend bridge in buffered mode.
3. Atomically append the complete staged transcript batch, ending in the active
   `LeafEntry`. This is the durable commit point.
4. Enter a serialized no-fail publication boundary.
5. For replacement, emit outgoing shutdown while its API is valid and clear its
   UI; handler failures become diagnostics.
6. Synchronically swap the active frontend session reference.
7. Attach/flush candidate UI and emit `session_start`; failures diagnose.
8. Commit staged trust for future operations; failure is fail-closed for future
   runs but does not roll back this already approved run.
9. Repair/update the session index; failure diagnoses and is repaired later.
10. Retire outgoing provider, tasks, and extension generation.

After step 3, expected extension, UI, trust-store, and index errors cannot escape
as ordinary rollback failures. A crash after step 3 leaves a complete leaf that
the next resume can recover.

`abort()` is idempotent. `adopt()` and `abort()` are mutually exclusive state
transitions.

### 7. Transcript is authoritative; index is repairable

Add optional `provider` to `ModelChangeEntry`. Every new initial selection and
switch writes it. A provider/model change and its final `LeafEntry` are one
atomic storage batch.

Selection precedence is:

1. explicit CLI provider/model override;
2. latest provider-aware model entry on the active transcript path;
3. session index provider only as a legacy anchor when path history lacks one;
4. durable default for a new session;
5. first usable credentialed durable provider for a new session;
6. TUI-only login-required bootstrap.

Dynamic local providers participate only through explicit selection, a stable
session reference, or an in-session action. They never silently enter step 5.

The append-only transcript wins over stale index metadata. Index update failure
cannot invalidate a committed switch; later startup repairs the index from the
transcript. Legacy entries remain readable and are not rewritten. Historical
cross-provider branches without provider identity remain inherently ambiguous
and use the record's best legacy provider anchor.

### 8. Switching and replacement are idle-only

Model/provider switching, reset, reload, resume, new-session, cwd replacement,
and branching reject while the harness is running with guidance to cancel first.
They do not close or replace an in-flight provider. Cancellation is not assumed
to mean drained; a future cancel-and-drain API may relax this only after streams
and tools have finished.

A switch transaction is candidate-first:

```text
validate effective provider/model
→ create candidate provider
→ prepare history/thinking/route/image state
→ atomic provider-aware ModelChangeEntry + LeafEntry batch
→ synchronous no-fail in-memory publication
→ repairable index update
→ close replaced provider
```

Any failure before the batch leaves provider, model, thinking, route, index,
transcript, and active runtime unchanged.

### 9. Built-in provider identifier and coexistence

The canonical built-in provider ID and display name are **`llama.cpp`**.
Existing user catalog providers named **`llama-cpp`** remain distinct and are
not migrated, rewritten, hidden, or shadowed by name normalization. CLI/session
IDs are exact strings; punctuation is not canonicalized.

A user may also define a durable `llama.cpp` layer. Normal layer precedence
applies and diagnostics identify the effective source. Removing a dynamic layer
reveals the complete durable definition. Generic core code must not branch on
llama.cpp identifiers.

### 10. Stable scoped references

A built-in llama.cpp scoped reference uses the existing exact pair:

```json
{"provider": "llama.cpp", "model": "<server-reported-id>"}
```

It stores no endpoint, provider definition, headers, credentials, discovered
metadata, router state, or file path. Only trusted built-ins may opt into this
host-defined durable-reference scheme initially; user/project dynamic providers
cannot.

Resolution occurs only after the trusted built-in source and its endpoint-keyed
safe snapshot/live catalog are available. An absent, stale, sleeping, or unloaded
model remains visible as unavailable/inert where scoped references are managed.
It does not synthesize a model, probe the network, download, load, or switch.

### 11. Remaining bounded choices

These choices are fixed for later phases:

- module names: `session_preparation.py`, `extensions/provider_registry.py`, and
  `local_backends.py`;
- built-in state path: `TauPaths.home / "state/extensions/llama.cpp.json"`;
- named refresh timeout defaults: 5 seconds for startup resolution, 10 seconds
  for explicit `/model` refresh, and 15 seconds for `/local` operations;
- explicit resumed llama.cpp session with neither safe snapshot nor successful
  refresh: fail with actionable setup/retry guidance; never fall back or erase
  its stable reference;
- Phase 6 real adapter spike: Ollama, because its API/capabilities differ from
  llama.cpp while remaining easy to fake locally;
- hidden built-ins: included in detailed `/session` diagnostics with source
  `built-in`, omitted from ordinary install/discovery counts;
- safe state schema: unsupported versions are read-only errors, never
  destructively rewritten.

Timeout retains the previous safe snapshot and records one bounded diagnostic.
The numbers are defaults, not protocol claims.

## Consequences

### Positive

- TUI and print can select extension providers without trust-order duplication.
- Project code still cannot execute before trust.
- Provider/task cleanup has one owner.
- Resume and branching become provider-aware.
- Durable transcript commit and in-memory publication have a testable boundary.
- Existing `llama-cpp` custom providers remain compatible.
- `/local` stays provider-neutral and source-safe.

### Cost

- Session storage needs atomic batch append and cross-process locking.
- Frontend attachment needs buffering and contained diagnostics.
- `CodingSession.load()` must be split without losing its static-provider seam.
- Legacy cross-provider branches cannot be made unambiguous without rewriting
  history, which this design forbids.

## Characterization baseline

Phase 0 tests intentionally describe current behavior before refactoring:

- print startup and resume selection;
- TUI startup, explicit resume, and login-required bootstrap;
- project-trust cancellation during startup, reload, new, and destination
  adoption;
- fresh-runtime reload/new/resume behavior;
- branch model recovery and active-run rejection;
- provider construction failure preserving an active cross-provider selection;
- frontend/session cleanup at current ownership seams;
- exact coexistence of `llama.cpp` and `llama-cpp` identifiers.

Some tests expose behavior this ADR replaces, notably provider-before-extension
startup, split initial-provider ownership, model-only history, and TUI
cancel-before-replacement. Later phases must update those tests when the new
transaction lands rather than preserve obsolete behavior accidentally.

## Verification

Phase 0 runs the handoff's focused lifecycle pytest files, Ruff check, Ruff
format check, and mypy. No live provider, local server, historical branch, or
secret is required.
