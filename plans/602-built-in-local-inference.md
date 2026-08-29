# Issue #602 implementation plan: built-in local inference with llama.cpp

**Status:** Revised implementation plan (router management required)
**Umbrella issue:** [#602 — Built-in local inference: `/local` with llama.cpp as the default backend](https://github.com/huggingface/tau/issues/602)
**Supersedes:** #415, #443, and draft PR #417
**Tau implementation baseline:** `origin/main` at `9c1285d68f41059105bd8a834d309a4d601a3b07`
**Pi reference baseline:** `eb1f87fa9a29e27e0c63dcb40dbed9a3624c82b1`
**Plan type:** Multi-PR implementation; not one monolithic change

> Baseline refreshed before implementation. All umbrella work starts from the exact `origin/main` commit above; historical prototype branches remain evidence only.

## 1. Objective

Give Tau a first-class built-in local-inference experience:

```text
/local
  llama.cpp  <- initial and default local backend
  Ollama     <- possible future backend
  vLLM       <- possible future backend
  LM Studio  <- possible future backend
```

The first shipped backend is llama.cpp. It is bundled with Tau as a trusted hidden built-in extension, but it registers through generic extension APIs rather than adding llama.cpp branches throughout Tau core.

The implementation must also establish the reusable foundation needed by future local or remote dynamic providers:

- trusted hidden built-in extensions;
- process-local dynamic provider objects;
- source- and generation-aware provider overlays;
- asynchronous model discovery and safe snapshot publication;
- required, optional, and absent authentication;
- one trust-aware session preparation path shared by TUI and print mode;
- failure-safe provider/model switching;
- a provider-neutral local-backend registry and `/local` host experience;
- correct reload, resume, cwd replacement, cancellation, and resource cleanup.

## 2. Decision summary

1. **Treat #602 as an umbrella.** Deliver it through small child issues and reviewable PRs.
2. **Use `/local`, not `/llama`.** `/local` is owned by Tau and aggregates generic local backends.
3. **Bundle llama.cpp through the extension system.** It is trusted and hidden, but lifecycle-managed and failure-isolated like another extension.
4. **Prefer `llama.cpp` as the canonical provider ID, subject to a Phase 0 ruling.** It aligns with Pi and avoids colliding with existing user-created `llama-cpp` catalog entries, but CLI/catalog/session portability and coexistence must be tested before freezing it. Display name is `llama.cpp`.
5. **“Default” means recommended local backend.** `/local` always presents a backend choice and marks llama.cpp as recommended/preselected, even while it is the only implementation. The user confirms it; llama.cpp never silently replaces Tau's global startup provider.
6. **Keep dynamic definitions process-local.** Never copy a dynamic provider into `catalog.toml` or the durable provider-definition schema.
7. **Persist references and safe snapshots only.** Session records may retain `provider="llama.cpp"` and a model ID. A versioned built-in-integration store may retain endpoint and safe model snapshots. Secrets stay in Tau's credential store or environment.
8. **Reuse Tau's OpenAI-compatible transport.** No llama.cpp streaming implementation belongs in `tau_agent` or the `/local` host.
9. **Do not require fake authentication.** No key means no `Authorization` header. Do not synthesize `Bearer local`.
10. **Do not guess metadata.** Unknown context windows, output limits, reasoning, modality, pricing, and compatibility remain unknown.
11. **Make basic inference independent of router mode, but deliver router management.** A normal single-model OpenAI-compatible `llama-server` must work. For a compatible router, `/local` must also support explicit Hugging Face search/download and model load/unload without making router mode a prerequisite for inference.
12. **Load project code only after trust.** Dynamic provider startup must not bypass the current staged project-trust lifecycle.
13. **Use one staged preparation service.** TUI startup, print startup, print resume, TUI resume/new, and reload must not each invent their own provider/extension ordering.
14. **Make one owner close each resource.** A prepared/adopted `CodingSession` owns its provider, provider-refresh tasks, and extension generation; frontends do not also close the same provider.
15. **Validate generic APIs with a second backend.** A deterministic fake is required. A small Ollama, vLLM, or LM Studio spike is preferred before declaring the public API stable.

## 3. Scope and release boundary

### 3.1 Required for the first complete release

- trusted hidden built-in extension loading;
- generic dynamic provider API;
- dormant zero-model providers;
- provider-supplied safe initial model snapshots, plus a versioned built-in llama.cpp disk cache;
- async refresh with timeout, cancellation, shared work, and stale-generation protection;
- optional/no-auth OpenAI-compatible provider support;
- one shared session/provider preparation path;
- model picker and startup selection integration;
- generic local-backend registry;
- `/local` setup, status, model discovery, selection, doctor, and reset experiences;
- built-in llama.cpp connection and inference support;
- standard OpenAI-compatible discovery for single-model servers and loaded router models;
- llama.cpp router model-state discovery, loading, and unloading;
- Hugging Face GGUF repository search, quantization selection, and server-side download;
- progress, cancellation, retry, and shared-router confirmations;
- safe durable scoped-model references for built-in dynamic providers without persisting their definitions;
- deterministic fake-server and fake-Hugging-Face tests;
- validation against a fake second local backend;
- published docs and architecture notes.

### 3.2 Required sequencing within the complete release

Basic single-model connection and inference land before router management so the protocol-specific work builds on stable generic APIs. Phase 7 is nevertheless required before the umbrella branch and issue #602 are considered complete. A shipped second backend remains optional; the permanent fake second backend and real adapter spike remain required validation.

## 4. Current behavior on Tau `main`

### 4.1 Extension lifecycle

Relevant modules:

- `src/tau_coding/extensions/api.py`
- `src/tau_coding/extensions/loader.py`
- `src/tau_coding/extensions/runtime.py`
- `src/tau_coding/project_trust.py`
- `src/tau_coding/session.py`

Current behavior:

1. `CodingSession.load()` reads the transcript.
2. It creates a fresh cwd-bound `ExtensionRuntime`.
3. It loads eligible user and explicit extensions, but not project extensions.
4. `ProjectTrustCoordinator.resolve()` determines whether protected project inputs are eligible.
5. It loads trusted resources and, with `--project-extensions`, trusted project extensions.
6. It composes tools, commands, guidelines, and the system prompt.
7. It builds the `AgentHarness` with a provider that the caller already created.
8. The frontend attaches UI and releases pending `session_start`.
9. Trust is committed only after staged session adoption.

A fresh runtime is staged for reload and destination-session replacement. The old runtime is retired only after preparation succeeds. This prevents extension state from crossing cwd trust boundaries.

Current extensions cannot register providers or local backends.

### 4.2 Provider lifecycle

Relevant modules:

- `src/tau_coding/provider_catalog.py`
- `src/tau_coding/catalog_loader.py`
- `src/tau_coding/provider_config.py`
- `src/tau_coding/provider_runtime.py`
- `src/tau_ai/env.py`
- `src/tau_ai/openai_compatible.py`

Current behavior separates:

- bundled catalog definitions;
- user catalog overlays in `~/.tau/catalog.toml`;
- runtime preferences in `~/.tau/providers.json`;
- credentials in `~/.tau/credentials.json`.

It assumes provider definitions are durable. Selection, model validation, thinking preferences, scoped-model preferences, and runtime creation all operate on `ProviderSettings`.

`OpenAICompatibleConfig` can omit the authorization header, but current durable-provider credential gates require an API key. Dynamic optional/no-auth providers need a separate generic auth contract rather than a fake environment value.

### 4.3 Startup inversion that must be fixed

Both frontends currently resolve and construct a provider before calling `CodingSession.load()`:

```text
load durable settings
→ resolve provider/model
→ create runtime provider
→ call CodingSession.load()
→ load extensions and resolve project trust
```

Therefore an extension provider cannot satisfy:

```bash
tau --provider llama.cpp --model <id>
tau --provider llama.cpp --model <id> --print "hello"
```

without a new preparation boundary.

The new required order is:

```text
resolve destination cwd/session
→ create fresh extension runtime
→ load trusted built-ins and eligible extensions
→ resolve project trust
→ load trusted project extensions
→ restore safe provider snapshots
→ compose effective provider layers
→ refresh an explicitly requested provider when needed
→ resolve provider/model
→ create candidate provider
→ build coding session
→ attach frontend and emit session_start
→ commit staged trust/session
→ retire old generation
```

### 4.4 Existing local-model workaround

Users can manually add an OpenAI-compatible provider through `/login custom`, `tau setup`, or `~/.tau/catalog.toml`. The current llama.cpp guide requires a fake non-empty key and often a fake model ID.

The new feature must not break manually configured local providers. Existing `llama-cpp` user catalog entries continue to work. The built-in provider uses the distinct canonical ID `llama.cpp`.

## 5. Desired user experience

### 5.1 First connection

The user starts llama.cpp independently:

```bash
llama-server -hf <tool-capable-gguf>
```

Then runs Tau and opens:

```text
/local
```

`/local` first presents registered local backends. llama.cpp is marked **Recommended** and preselected, including while it is the only backend, but the user confirms the choice. This keeps the entry point provider-neutral and makes adding LM Studio, Ollama, vLLM, or another backend a registration change rather than a command redesign.

The llama.cpp wizard:

1. Offers the saved endpoint, then `LLAMA_BASE_URL`, then `http://127.0.0.1:8080` according to the documented precedence.
2. Probes only that known configured/default endpoint; it does not scan processes, ports, or the local network.
3. Accepts a URL with or without `/v1` and normalizes it.
4. Accepts an optional API key through a secret host input.
5. Distinguishes unreachable, timeout, loading, authentication, malformed-response, single-model, and router-mode results.
6. Discovers real loaded model IDs through the OpenAI-compatible API and, in router mode, lists loaded and unloaded router models with their server-reported states.
7. In router mode, allows explicit load/unload and Hugging Face GGUF search/download, including quantization selection, progress, cancellation, retry, gated-repository guidance, and shared-router confirmation.
8. Selects the only loaded model automatically, or shows a picker when several loaded models are available.
9. Requires an explicit model in headless use when selection is ambiguous.
10. Stores only endpoint, safe discovery data, selected model, and stable scoped-model references outside the credential store.
11. Makes loaded provider models appear immediately in `/model` without restart; unloaded models remain management choices, not inference choices.
12. Offers to switch the current session only after a candidate runtime is ready.

Example feedback:

```text
✓ Found llama.cpp at http://127.0.0.1:8080/v1
✓ Discovered model: qwen3-coder.gguf
✓ Ready: llama.cpp:qwen3-coder.gguf
```

### 5.2 `/local` information architecture

The host-owned `/local` view supports:

- backend selection when more than one backend exists;
- setup/configure;
- connection status;
- model refresh;
- current and cached models;
- use/select model;
- doctor/compatibility diagnostics;
- reset integration settings;
- backend-contributed model-management actions when supported.

The view renders only capabilities supported by the selected backend. The llama.cpp backend must contribute router management when a compatible router is detected, while single-model servers remain fully usable without management controls.

There is no `/llama` or `/llama-cpp` alias.

### 5.3 Model selection and startup

After setup:

```text
/model
```

shows the provider display name and discovered model display names. Explicit startup works:

```bash
tau --provider llama.cpp --model <model-id>
tau --provider llama.cpp --model <model-id> --print "summarize this project"
```

Selection rules:

- With a valid cached snapshot, startup does not require a successful network refresh.
- If an explicitly requested provider/model has no safe snapshot and cannot be discovered, fail with an actionable error; never silently choose a different explicit model.
- If llama.cpp was not requested and is unavailable, it does not block ordinary startup or become a fallback candidate merely because it is built in.
- Preserve Tau's current no-credential TUI bootstrap: when no ordinary provider is usable, the TUI still opens with its login-required provider so `/login` and `/local` remain available. Print mode fails non-interactively with an actionable error.
- A resumed llama.cpp session restores its stable provider/model reference before validation. Network discovery failure does not erase the reference or cached snapshot.
- If a refreshed catalog no longer includes the active model, keep the active runtime usable, mark it stale in diagnostics, and prevent new selection of the missing model until it reappears.

### 5.4 Doctor flow

The llama.cpp backend provides a deterministic doctor action that reports stages independently:

1. endpoint normalization and server reachability;
2. health/loading state when available;
3. model discovery;
4. authentication acceptance;
5. streaming chat completion;
6. tool schema acceptance;
7. actual tool-call emission.

A server that streams but whose model does not call the probe tool receives a warning, not a connectivity failure. Explain likely causes:

- model is not a tool-capable instruct model;
- GGUF/chat template lacks tool-call support;
- `--jinja` or another server flag is needed;
- llama.cpp needs updating.

Doctor probes must be explicit user actions. Do not spend model tokens or execute compatibility requests during ordinary startup.

### 5.5 Reset

Reset:

- confirms safe-state reset and stored-credential deletion separately;
- requires the session to be idle;
- if the active runtime uses the built-in llama.cpp layer, first asks the user to choose another usable provider; the login-required bootstrap is acceptable in the TUI when no provider is usable, while headless reset is unsupported;
- aborts without deleting anything if the fallback switch is cancelled or fails;
- after a successful switch, removes only llama.cpp built-in-owned non-secret settings, cached snapshots, and provider/backend layer;
- removes the referenced stored credential last when separately confirmed; deletion failure leaves an unreferenced credential and an actionable cleanup diagnostic, not an active partial configuration;
- removes or replaces only the built-in source's layer, never another source's override;
- reports when the built-in layer is shadowed by another source;
- never stops the external server;
- never deletes model files.

## 6. Architecture and package ownership

```text
tau_ai
  existing OpenAI-compatible transport and conditional auth header

tau_agent
  provider-neutral harness, messages, events, tools, sessions
  generic atomic session-entry batch append and provider-aware model history

tau_coding
  built-in extension registry
  dynamic provider contracts and registry
  trust-aware session preparation
  local-backend registry and /local host UI
  extension-scoped safe state and credential integration

built-in llama.cpp extension
  endpoint normalization
  discovery and status
  diagnostics
  OpenAI-compatible provider descriptor
  optional router capabilities
```

### 6.1 Hard boundaries

- `tau_agent` must not import extension, catalog, credential, local backend, llama.cpp, Rich, or Textual code.
- `tau_ai` may expose transport-level generic options but must not know about extensions or `/local`.
- Provider composition, trust, persistence, session startup, and local UI belong to `tau_coding`.
- llama.cpp URLs, endpoints, response shapes, and diagnostics belong to its built-in extension package.
- Textual widgets remain behind `tau_coding.tui`; the provider and backend contracts are UI-toolkit-neutral.

### 6.2 Proposed module layout

Names may be refined, but responsibilities should remain separated:

```text
src/tau_coding/
  built_in_extensions.py       built-in extension declarations and source metadata
  session_preparation.py       shared staged startup/resume/replacement orchestration
  local_backends.py            LocalBackend contract and runtime-local registry
  provider_config.py           durable settings only
  provider_runtime.py          durable + dynamic runtime construction dispatcher

  extensions/
    api.py                     public register_provider/register_local_backend actions
    runtime.py                 generation owner and registration routing
    providers.py               DynamicProvider, ProviderModel, auth and transport contracts
    provider_registry.py       layered runtime-local registry and refresh coordinator
    builtins/
      llama_cpp/
        extension.py           setup() registrations
        client.py              llama.cpp HTTP API and response parsing
        provider.py            provider/model conversion and refresh
        backend.py             /local capabilities, setup, doctor, reset
        state.py               versioned non-secret settings/snapshot persistence
```

Do not put all provider lifecycle logic into `extensions/runtime.py`; keep it a coordinator rather than another provider configuration monolith.

## 7. Public contracts and data models

The sketches below define responsibilities, not final spelling. Types must be typed dataclasses or protocols, immutable where practical.

### 7.1 Dynamic models

```python
@dataclass(frozen=True, slots=True)
class ProviderModel:
    id: str
    display_name: str | None = None
    api: ProviderApi | None = None
    base_url: str | None = None
    reasoning: bool | None = None
    input_modalities: tuple[str, ...] | None = None
    context_window: int | None = None
    max_tokens: int | None = None
    cost: ModelCost | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    compat: Mapping[str, JSONValue] = field(default_factory=dict)
    thinking_levels: tuple[ThinkingLevel, ...] | None = None
```

Rules:

- `id` is required and non-empty.
- Unknown metadata uses `None`, never an invented fallback.
- An empty tuple means “known to support none”; `None` means unknown.
- Model-level headers are runtime data and are excluded from persisted safe snapshots unless explicitly proven non-secret.
- Duplicate model IDs reject the candidate snapshot atomically.

### 7.2 Authentication

```python
class ProviderAuth(Protocol):
    async def resolve(self, context: ProviderAuthContext) -> ResolvedProviderAuth: ...

@dataclass(frozen=True, slots=True)
class ResolvedProviderAuth:
    api_key: str | None
    headers: Mapping[str, str]
    source: str
    omit_authorization_header: bool
```

Provide generic strategies:

- `RequiredApiKey(credential_name, env_var)`;
- `OptionalApiKey(credential_name, env_var)`;
- `NoAuth()`.

Resolution order for key strategies:

1. Tau credential store;
2. configured environment variable;
3. missing result.

Required auth fails with actionable setup guidance. Optional auth returns no key and sets `omit_authorization_header=True`. No-auth never consults credentials and always omits the header.

Never place secret values in reprs, diagnostics, snapshots, settings, or session metadata.

### 7.3 Transport/runtime creation

```python
@dataclass(frozen=True, slots=True)
class OpenAICompatibleTransport:
    base_url: str
    api: ProviderApi = "openai-completions"
    auth: ProviderAuth = field(default_factory=NoAuth)
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float = DEFAULT_...
    max_retries: int = DEFAULT_...
    max_retry_delay_seconds: float = DEFAULT_...

RuntimeFactory = Callable[
    [ProviderRuntimeContext, ProviderModel],
    Awaitable[ClosableModelProvider] | ClosableModelProvider,
]
```

A `DynamicProvider` supplies either a generic transport descriptor or a runtime factory. OpenAI-compatible providers use the existing `OpenAICompatibleProvider` path. A custom runtime factory supports future non-OpenAI-compatible extensions without putting provider-specific assumptions into the registry.

Reusing the transport requires a compatibility audit: dynamic providers that select `openai-completions` must not inherit first-party endpoint routing solely from model-name heuristics. Test model IDs such as `gpt-*` and `o*` against a local server and add an explicit provider-scoped API choice where necessary.

The host resolves auth immediately before candidate runtime creation. The runtime factory must not mutate active session state.

### 7.4 Provider object

```python
@dataclass(frozen=True, slots=True)
class DynamicProvider:
    id: str
    display_name: str
    models: tuple[ProviderModel, ...] = ()
    default_model: str | None = None
    transport: OpenAICompatibleTransport | None = None
    runtime_factory: RuntimeFactory | None = None
    refresh_models: RefreshModels | None = None
```

Validation:

- provider ID and display name are non-empty;
- zero models is valid;
- `default_model`, when present, belongs to the snapshot;
- exactly one runtime mechanism is usable;
- provider/model metadata is JSON-safe where it crosses persistence boundaries;
- invalid replacement leaves the prior layer untouched.

The API shape exposed to an extension:

```python
def setup(tau):
    tau.register_provider(dynamic_provider)
    tau.register_local_backend(local_backend)
```

Do not expose a mutation-oriented `update_provider_models(...)` as the primary contract. Discovery returns a complete candidate snapshot and the registry publishes it atomically.

### 7.5 Refresh context and result

```python
@dataclass(frozen=True, slots=True)
class ProviderRefreshContext:
    signal: CancellationToken
    allow_network: bool
    cached_models: tuple[ProviderModel, ...]
    auth: ResolvedProviderAuth

RefreshModels = Callable[
    [ProviderRefreshContext],
    Awaitable[ProviderModelSnapshot],
]
```

Host refresh policy:

- accept a provider-supplied safe initial snapshot synchronously after that provider source is loaded and trusted;
- keep successful snapshots in memory for the generation;
- do not add a generic extension snapshot disk store in Phase 1: built-in llama.cpp owns its versioned allowlisted cache in Phase 5, and a future public extension-storage API requires a separate source-identity and trust design;
- use no network refresh for unrelated providers during startup;
- refresh an explicitly requested dormant provider when model resolution requires it;
- let `/local` and `/model` request explicit refreshes;
- coalesce concurrent refreshes for the same provider layer/generation;
- use a named, documented timeout constant;
- retain cache/current snapshot on timeout, cancellation, malformed output, or network error;
- publish only when source ID, provider layer ID, and generation still match;
- cancel owned work during retire/reset/reload;
- record one bounded diagnostic per failed refresh generation.

### 7.6 Local backend contract

The local-backend API describes capabilities and operations; it does not return Textual widgets.

```python
@dataclass(frozen=True, slots=True)
class LocalConfigField:
    key: str
    label: str
    kind: Literal["text", "secret", "choice"]
    required: bool = False
    placeholder: str | None = None
    choices: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class LocalConfigureSpec:
    fields: tuple[LocalConfigField, ...]

@dataclass(frozen=True, slots=True)
class LocalBackend:
    id: str
    provider_id: str
    display_name: str
    configure_spec: ReadConfigureSpec
    configure: ConfigureLocalBackend
    status: ReadLocalBackendStatus
    refresh: RefreshLocalBackend
    doctor: DoctorLocalBackend | None = None
    reset: ResetLocalBackend | None = None
    load_model: ManageModel | None = None
    unload_model: ManageModel | None = None
    download_model: DownloadModel | None = None
```

`register_local_backend()` stamps each registration with the extension source and generation. It also binds the backend to the provider layer registered by that same source, not merely to a provider ID. When another source shadows the paired provider layer, `/local` may still show status/configuration but must mark `use` unavailable until that provider layer becomes effective. Reset removes only registrations and state owned by its source.

Configuration is a host-rendered transaction:

1. Backend returns a structured field specification.
2. Host collects normal and secret values without logging or persisting them.
3. Host submits one ephemeral values object to the backend.
4. Backend validates connectivity and returns field/global errors or a complete candidate configuration.
5. Backend commits configuration only after validation succeeds, using the cross-store protocol below.
6. Cancellation leaves prior settings, credentials, provider snapshot, and active runtime untouched. A write failure leaves either the prior committed configuration or a recoverable, unreferenced credential orphan—never a partially selected provider configuration.

For a stored key, use a generation-specific credential name such as `llama.cpp:<random-id>`. The safe state contains only this non-secret `credential_ref`; the dynamic provider constructs `OptionalApiKey(credential_name=credential_ref, env_var="LLAMA_API_KEY")`. Write the new credential first, then atomically commit safe state that references that credential generation; the safe-state write is the configuration commit point. A crash before the state commit leaves an unreferenced credential that startup cleanup can remove by the integration-owned prefix. After the state commit, delete the old referenced credential best-effort. Reset deletes only the current referenced credential after separate confirmation. Diagnostics collapse any generation to the source label `stored credential` and never print the reference. Never journal plaintext secrets. Environment-only/no-auth setup stores `credential_ref=null`. Test failure and process-recovery behavior after every store operation.

A backend may return a revised specification for a second step, but it does not construct Textual widgets or receive raw UI internals. Phase 4 must prove this with two fake backends requesting different normal, secret, and choice fields.

Results are structured host-renderable values:

- connection state;
- endpoint display value;
- authentication source without secret values;
- discovered/cached models and status;
- selected model;
- supported actions;
- progress events;
- actionable diagnostics.

The host controls secret input, confirmation, selection, cancellation, notification, and rendering. Backend code controls protocol operations and messages specific to its server.

## 8. Provider overlay and ownership semantics

### 8.1 Layer model

The effective provider view is composed without modifying durable settings:

```text
provider ID
  durable catalog/settings baseline
  built-in extension layer
  user extension layer
  explicit extension layer
  trusted project extension layer  <- effective when present
```

Use stable source IDs and generation IDs, not only provider names.

Recommended precedence is registration order with latest active layer effective. With current staged loading, this gives durable < built-in < user < explicit < trusted project. Document this deliberate difference from first-registration-wins tool/command registries: provider definitions are replaceable configuration layers, and removal must reveal the previous complete definition.

Required operations:

- atomic layer registration;
- atomic same-source replacement;
- multiple sources with the same provider ID;
- unregister one source without deleting other layers;
- restore the preceding complete dynamic layer;
- restore the complete durable provider definition after the final dynamic layer disappears;
- remove all layers/tasks owned by a retired generation;
- reject publication from stale refreshes;
- expose source information for diagnostics without exposing secrets.

### 8.2 Registry lifetime

Each staged `ExtensionRuntime` owns a `DynamicProviderRegistry` and `LocalBackendRegistry`. They are not process-global singletons.

A runtime may receive an immutable durable-provider baseline and safe cache store, but its active registrations and tasks belong only to that runtime generation. Resume/new/cwd replacement stages a fresh registry and reconstructs built-in/user/project registrations under the destination trust decision.

This is the central change from PR #417's older long-lived-runtime assumption.

### 8.3 Runtime resource ownership

- A prepared session owns its candidate model provider after construction.
- On failed preparation, the preparation object closes the candidate and retires the staged extension/provider runtime.
- On successful adoption, ownership transfers exactly once to `CodingSession`.
- Provider/model switching, reset, reload, resume, new-session, and cwd replacement require an idle harness. If a run is active, reject the action with guidance to cancel first; do not close an in-flight provider. A future cancel-and-drain operation may relax this only after it awaits stream and tool shutdown completely.
- On successful idle model switch, close the replaced Tau-owned provider immediately after the no-fail publication boundary.
- On failed model switch, close only the failed candidate and leave the current runtime untouched.
- On reload/resume/new/cwd replacement, the outgoing session remains alive until the candidate is fully prepared.
- On final close, cancel refresh tasks, emit shutdown, retire extension generation, and close all remaining owned providers.
- Remove frontend-level duplicate provider closing after session ownership is unified.

## 9. Shared trust-aware session preparation

### 9.1 Extract preparation from `CodingSession.load()`

Introduce a staged service in `src/tau_coding/session_preparation.py`. Do not bolt extension loading separately into `cli.py` and `tui/app.py`.

Suggested internal types:

```python
@dataclass(frozen=True, slots=True)
class SessionPreparationRequest:
    storage: SessionStorage
    destination_cwd: Path
    session_record: CodingSessionRecord | None
    requested_provider: str | None
    requested_model: str | None
    provider_override_is_explicit: bool
    model_override_is_explicit: bool
    # resource, extension, trust, prompt, and runtime options

class PreparedCodingSession:
    session: CodingSession
    async def adopt(...): ...
    async def abort(...): ...
```

Extract `CodingSession.load()` into three conceptual stages:

1. **State stage:** read transcript/session state and determine destination metadata without creating a provider.
2. **Environment stage:** load built-ins/extensions, resolve trust, load protected resources, restore provider snapshots, and compose the effective provider view.
3. **Runtime stage:** resolve provider/model, create the candidate runtime, build/bind the harness and coding session.

Keep a compatibility wrapper for tests and internal consumers that directly provide a static `ModelProvider`, but route CLI/TUI application startup through the shared staged service.

### 9.2 Selection precedence

Provider/model selection order remains predictable:

1. explicit CLI `--provider` and/or `--model` overrides;
2. the active provider-aware `ModelChangeEntry` on the resumed transcript leaf;
3. the session index/record only as a legacy provider anchor when the active transcript path has no provider-aware entry;
4. durable user default for a new session;
5. first usable credentialed durable provider for a new session;
6. TUI-only login-required bootstrap when none is usable.

The transcript is authoritative after a committed switch; stale index metadata must never override it. The index remains a locator/cache and is repaired from the transcript. Print mode has no step 6 and exits with an actionable non-interactive error. Dynamic local providers do not automatically enter step 5. They are selected only by explicit CLI choice, an existing session reference, or a current-session `/model`/`/local` action.

Resolve the effective provider view before validating any dynamic provider/model reference.

Preserve existing special behavior:

- Hugging Face logical provider and inference-provider pins;
- provider schema migration;
- Codex runtime limits discovery;
- provider-anchored context recovery;
- image-input metadata;
- thinking-level compatibility;
- system-prompt startup overrides;
- print-mode session resume.

### 9.3 Adoption sequence

Preparation is read-only with respect to authoritative session storage: transcript initialization, repair entries, provider/model changes, and leaf updates remain staged in memory. Introduce a per-session cross-process lock file and migrate every JSONL read, normal append, and new batch append through it (shared reads where supported; exclusive writes). Extend the provider-neutral `SessionStorage` contract with an atomic batch append. `JsonlSessionStorage` acquires the exclusive lock, re-reads the current file, writes current bytes plus the complete batch to a same-directory temp file, flushes and `fsync`s it, atomically replaces the JSONL, then `fsync`s the directory. Normal append also takes the exclusive lock, so it cannot be lost during replacement. In-memory storage applies a batch under one async lock. A failed batch leaves the prior file unchanged; recovery rejects or safely removes an incomplete temp artifact. Test concurrent processes/tasks performing normal append versus batch replacement and define platform behavior when advisory locks are unavailable. The batch is the durable adoption commit.

For startup:

1. Prepare session, staged entries, extension generation, and candidate provider without authoritative writes.
2. Attach the frontend UI bridge in buffered/staged mode.
3. Atomically append the staged entry batch, including its final `LeafEntry`; this is the durable commit point.
4. Publish the prepared session as the active frontend session through a synchronous no-fail assignment.
5. Flush/attach UI and emit pending `session_start`; contain handler/widget failures as diagnostics.
6. Commit staged trust only after adoption. A trust-store failure is fail-closed for future runs and does not invalidate this approved run-only snapshot.
7. Update the rebuildable session index; failure becomes a repair diagnostic.
8. Begin accepting user input.

For replacement/reload, first require an idle harness. Then use the same commit boundary:

1. Prepare the full destination snapshot and all repair/initial entries without mutating current or destination authoritative storage.
2. Attach the candidate UI bridge in buffered/staged mode that cannot replace outgoing components yet.
3. Atomically append the complete destination entry batch, ending in the intended `LeafEntry`. If the batch fails, abort the candidate and keep both sessions' authoritative state unchanged.
4. Enter a serialized **no-fail publication boundary**. After this point, expected extension/UI/storage errors are contained as diagnostics rather than raised.
5. Emit outgoing `session_shutdown` while its API remains valid; contain handler failures.
6. Clear outgoing extension UI.
7. Atomically swap the frontend's active session reference.
8. Flush/attach candidate UI and emit candidate `session_start`; contain handler/widget failures.
9. Commit the staged trust-store decision only after successful adoption. A trust-store write failure is fail-closed for future runs, becomes a diagnostic, and does not invalidate the approved run-only snapshot.
10. Update the rebuildable session index; failure becomes a repair diagnostic.
11. Retire and close outgoing provider/extension resources.

A process crash after the atomic entry batch but before in-memory publication leaves a fully committed destination leaf that the next resume can recover. The implementation must document which operations occur before and inside the no-fail boundary and test failures at every batch/index/trust step. Trust must never be durably written for an aborted or incomplete candidate. Unexpected post-boundary failures are host bugs contained where possible, not ordinary rollback paths.

### 9.4 Model switching

Replace current mutation-first switching with an idle-only, async candidate-first transaction:

```python
async def select_provider_model(provider_id: str, model_id: str) -> ModelSelectionResult:
    reject while harness is running
    validate against effective snapshot
    create candidate runtime
    prepare history/tool compatibility and next thinking/route state
    atomically append provider-aware ModelChangeEntry + LeafEntry as the durable batch
    publish the already-prepared in-memory provider/model state without awaiting
    update the repairable session index cache
    close replaced owned runtime
```

The append-only transcript is authoritative; the session index is a rebuildable cache. `ModelChangeEntry` and its active `LeafEntry` are one atomic storage batch. A failed batch aborts and closes the candidate without an orphan entry or switch. Once the batch is durable, publication is a synchronous no-fail assignment. An index update failure records a diagnostic and leaves the committed switch intact for later index repair.

Extend `ModelChangeEntry` with an optional `provider` field and write it for every new initial/switch entry. Replay and branch recovery use the latest provider-aware entry. Legacy entries inherit the best available legacy provider anchor from the session record; do not rewrite old JSONL automatically. Document the unavoidable ambiguity in old cross-provider branches and add compatibility, branch, export, and downgrade tests.

Failure before the durable commit leaves active provider, model, thinking, inference route, session metadata, and runtime unchanged.

TUI picker callbacks and extension actions must await this path. Do not call synchronous `set_model()` and then attempt provider refresh.

## 10. Built-in extension mechanism

### 10.1 Declaration

Add a generic declaration similar in spirit to Pi:

```python
@dataclass(frozen=True, slots=True)
class BuiltInExtension:
    name: str
    setup: ExtensionSetup
    hidden: bool = True
```

Initial registry:

```python
BUILT_IN_EXTENSIONS = (
    BuiltInExtension(name="llama.cpp", setup=llama_cpp.setup, hidden=True),
)
```

### 10.2 Loading rules

- Built-ins load before user/explicit/project extensions.
- Built-ins load even with `--no-extensions` because that flag disables discovered extension directories, not product capabilities bundled with Tau.
- Built-ins are trusted and do not participate as protected project input.
- Built-ins may handle project trust only if deliberately allowed by the generic contract; llama.cpp does not need to.
- Built-in `setup()` remains synchronous and registration-only.
- Network work begins only through refresh or local-backend actions.
- Setup failures become extension diagnostics and do not crash Tau.
- Hidden built-ins are omitted from ordinary extension installation/discovery lists, but `/session` and diagnostics may identify them as built-in sources.
- Reload creates a new built-in generation and cancels the old generation's work.
- A minimal fake built-in extension must prove loading, diagnostics, generation invalidation, and `--no-extensions` behavior before llama.cpp lands.

## 11. llama.cpp built-in implementation

### 11.1 Connection settings

Preferred values, finalized by the Phase 0 identifier ruling:

- provider ID candidate: `llama.cpp`;
- display name: `llama.cpp`;
- default server URL: `http://127.0.0.1:8080`;
- inference URL: normalized OpenAI-compatible `/v1` base;
- integration-owned credential-name prefix: `llama.cpp:` followed by an opaque generation ID stored as a non-secret reference;
- optional environment endpoint: `LLAMA_BASE_URL`;
- optional environment key: `LLAMA_API_KEY`.

Endpoint precedence is:

1. an in-progress explicit `/local` setup value;
2. saved endpoint state;
3. `LLAMA_BASE_URL` for the current process when no endpoint has been saved;
4. the default offered by `/local`.

An explicit successful setup therefore remains effective even when `LLAMA_BASE_URL` exists. The environment endpoint is never copied into saved state unless the user explicitly confirms it through setup. The default alone does not mean configured and does not trigger startup network access. A saved endpoint or non-empty environment endpoint means configured. Cached models are keyed to the normalized effective endpoint and are ignored when that endpoint changes.

URL normalization must:

- require `http` or `https`;
- reject credentials embedded in the URL;
- reject query/fragment components for the base endpoint;
- normalize trailing slashes;
- accept either server root or `/v1` input;
- retain an unambiguous server root for health/router calls and an inference base for OpenAI-compatible requests;
- avoid silently rewriting remote hosts to localhost.

### 11.2 Basic discovery

Basic mode uses documented, compatible endpoints and defensive parsing:

- discover OpenAI-compatible model IDs from `/v1/models`;
- optionally inspect health/loading state where available;
- accept one or several models;
- keep the provider dormant when no model is selectable;
- retain a cached safe snapshot after temporary failure;
- distinguish malformed success responses from transport errors;
- surface HTTP 401/403 with `LLAMA_API_KEY`/credential guidance;
- never persist response headers, keys, or arbitrary server payloads.

Single-model mode is sufficient for first release. Standard `/v1/models` may also expose currently loaded router models. Router-specific discovery of unloaded models and router management are Phase 7; router metadata may enrich Phase 5 status only when fields are present and trustworthy.

### 11.3 Metadata conversion

For each discovered model:

- use the server's exact model ID;
- use a reported display name when present, otherwise the ID;
- use a positive server-reported context window only when the meaning is documented;
- infer image input only from reliable server architecture/modalities data;
- report zero cost only as “local/unpriced” if the UI can distinguish that from a verified zero commercial price; otherwise leave pricing unknown;
- leave reasoning, output limit, tool support, and compatibility unknown unless directly verified;
- distinguish unknown provider metadata from Tau's operational context fallback: `/session` and diagnostics show the metadata source as unknown/fallback, while compaction may use Tau's existing conservative fallback solely to remain operable;
- image support remains unavailable unless positively reported, and context accounting must not present an operational fallback as a server claim;
- do not copy Pi's 128K fallback;
- do not set `max_tokens=context_window` merely because the context window is known.

### 11.4 Authentication

- Endpoint configuration counts as configured even without a key.
- Stored credential wins over `LLAMA_API_KEY`.
- No key produces no `Authorization` header.
- A key produces `Authorization: Bearer <key>` through the existing transport.
- Setup accepts secrets only through host secret input.
- Reset handles settings and credential removal separately.
- Diagnostics show only `none`, environment variable, or stored credential.

### 11.5 Safe state

Create a versioned, locked, atomically replaced, user-level state file for built-in integration data, for example:

```text
~/.tau/state/extensions/llama.cpp.json
```

Final path should follow existing `TauPaths` conventions and be documented. Suggested schema:

```json
{
  "schema_version": 1,
  "endpoint": "http://127.0.0.1:8080",
  "selected_model": "qwen3-coder.gguf",
  "credential_ref": "llama.cpp:<opaque-generation-id>",
  "models": [
    {"id": "qwen3-coder.gguf", "display_name": "qwen3-coder.gguf"}
  ],
  "checked_at": "2026-08-18T12:00:00Z"
}
```

The snapshot schema must allow only the safe metadata subset. Unknown fields are either ignored for forward compatibility or rejected according to one documented versioning policy. Unsupported schema versions must not be destructively rewritten.

Do not store:

- API keys;
- arbitrary headers;
- server PIDs;
- guessed metadata;
- downloaded file paths unless a later router feature explicitly needs and secures them;
- project-local endpoint overrides.

### 11.6 Required router capability phase

After basic inference is stable, research the current llama.cpp router API against official docs and pin a tested version range. Router support is capability-detected and version-gated; an unknown or incompatible router degrades to standard OpenAI-compatible discovery rather than risking a mutating request.

Required operations for a compatible router:

- list router models and server-reported states;
- load one model and wait for reconciled loaded state;
- unload one model after explicit confirmation;
- search Hugging Face for GGUF repositories using authenticated or anonymous API access;
- inspect repository gating and GGUF files, present quantizations and sizes when reported, and recommend `Q4_K_M` only as a UI preference rather than model metadata;
- ask the llama.cpp server to download the selected `owner/repository[:quantization]` model;
- cancel load/download through documented router operations;
- show bounded progress from router events/polling;
- retry connection and refresh without replaying an interrupted mutation;
- refresh the dynamic provider snapshot after every state transition.

Safety rules:

- never silently unload another model;
- show the router's current state before acting;
- ask whether to keep or unload existing models;
- cancellation never leaves Tau claiming a model is loaded before refresh confirms it;
- failed replacement attempts best-effort restore previously loaded models only after explicit user approval;
- gated repositories show the Hugging Face access URL and explain that the llama.cpp server process also needs an authorized `HF_TOKEN`;
- Tau may read `HF_TOKEN` and standard Hugging Face token-file locations for search, but never stores or forwards that token to llama.cpp; server-side download authentication belongs to the independently running server;
- never delete model files;
- never assume the router is private to Tau;
- router-only features remain hidden for single-model servers.

## 12. `/local` host implementation

### 12.1 Command integration

Add `/local` as a built-in command in `src/tau_coding/commands.py`. Extend `CommandResult` with a host action rather than implementing a synchronous extension command that spawns unmanaged tasks.

TUI flow:

```text
CommandRegistry.execute("local")
→ CommandResult(local_requested=True)
→ TauTuiApp opens LocalBackendScreen
→ screen calls async backend/service methods
```

This keeps async work, cancellation, and modal ownership in the TUI adapter.

Print mode does not execute interactive `/local`. Configuration for headless use relies on persisted setup plus `--provider`/`--model`. A future `tau local ...` CLI is out of scope unless a concrete automation need appears.

### 12.2 Generic TUI states

Cover:

- no backends registered;
- one backend, unconfigured;
- one backend, configured and reachable;
- one backend, configured but unavailable;
- multiple backends;
- dormant backend with zero models;
- cached snapshot with failed refresh;
- active model missing from current discovery;
- refresh in progress/cancelled/timed out;
- backend capability changes after refresh;
- setup and doctor progress;
- reset confirmation.

The screen uses generic status/action models. No `llama`, GGUF, router, quantization, or Hugging Face vocabulary belongs in generic TUI classes.

### 12.3 `/model` integration

- `available_model_choices` reads the effective provider view.
- Dynamic provider and model display names appear in the picker.
- Dormant zero-model providers do not create fake model rows.
- A refresh affordance may refresh dynamic providers explicitly; it must not synchronously probe every local endpoint when opening the picker.
- Dynamic provider definitions and model metadata are never copied into durable provider settings.
- A built-in dynamic provider may opt into a host-defined durable-reference scheme. For llama.cpp, scoped models persist only the stable provider ID and exact model ID using the existing scoped-model store; resolution succeeds only when the trusted built-in source is loaded and its endpoint-keyed safe snapshot or live catalog contains that model.
- Unloaded, missing, or stale scoped references stay inert and visible as unavailable rather than creating a fake model definition or triggering network/download/load work.
- User/project dynamic providers remain ineligible for scoped persistence until a future trusted stable-source identity contract exists. Thinking preferences remain disabled unless independently supported by verified metadata and a durable-reference contract.
- Selecting a dynamic model uses the async candidate-first selection path.

## 13. Persistence, migration, and security

### 13.1 Durable versus runtime data

| Data | Storage | Rule |
|---|---|---|
| Built-in/user provider catalog | bundled/user `catalog.toml` | Existing behavior |
| Provider preferences | `providers.json` | Dynamic definitions never copied here |
| API keys/OAuth | `credentials.json` or environment | Never in extension state/session/diagnostics |
| Dynamic provider layers | memory, owned by staged runtime | Removed on generation retirement |
| Built-in llama.cpp safe model snapshot | versioned built-in extension state | Keyed to endpoint; restored only after built-in source loads |
| Generic dynamic provider initial snapshot | provider registration/in-memory generation state | No generic disk persistence in this issue |
| Endpoint | versioned extension state | User-level only; never project-controlled by ambient settings |
| Active provider/model reference | session metadata/history | Stable ID/model only, not provider definition |
| Local global default | not supported initially | llama.cpp is only `/local`'s default backend |

### 13.2 Project trust

- Built-in extension code is trusted because it ships with Tau.
- User and explicit extensions remain eligible according to existing rules.
- Project extension discovery examines metadata only before trust.
- Project extension provider/backend registrations occur only after approval and `--project-extensions`.
- A project extension's provider-supplied initial snapshot cannot be registered before that exact source is trusted and loaded; generic project-extension snapshots are not restored from disk in this issue.
- Declining project trust produces a coherent global/explicit/built-in provider view.
- Cancelling trust during reload/resume keeps the current session and provider unchanged.
- Trust remains an input-loading guard, not a network/model/credential sandbox; docs must say so.

### 13.3 Backward compatibility

- Existing static providers and settings files continue to load unchanged.
- Existing `llama-cpp` custom providers are not migrated or shadowed by `llama.cpp`.
- Extend `ModelChangeEntry` with an optional `provider` field. Existing entries remain readable; every new initial and switch entry writes provider identity.
- New replay and branch recovery prefer provider-aware entries. Legacy entries use the session record's best available provider anchor and retain documented ambiguity for historical cross-provider branches.
- Do not rewrite existing JSONL automatically. Document that older Tau versions may reject or ignore the new optional field according to their strict wire model; add explicit downgrade guidance.
- Provider settings v2 migration remains authoritative for durable definitions.
- Dynamic provider save paths must bypass `save_default_provider_model`, `save_provider_thinking_level`, and `toggle_saved_scoped_model` unless operating only on a supported durable reference.

## 14. Implementation phases and PR sequence

Each phase should be a child issue with one or more atomic PRs. Do not begin the next user-facing layer until the previous phase's exit criteria pass.

### Phase 0 — lifecycle design and baseline refresh

**Goal:** Resolve the highest-risk preparation boundary before adding product UI.

Work:

1. Fetch current Tau `main` and current Pi.
2. Re-run the architecture audit for changed startup/session/provider code.
3. Write a focused ADR under `dev-notes/design/` describing:
   - state/environment/runtime preparation split;
   - trust ordering;
   - provider registry lifetime;
   - adoption/abort semantics;
   - provider/task ownership;
   - TUI/print shared entry point;
   - dynamic selection persistence rules;
   - provider-aware `ModelChangeEntry` replay and legacy fallback;
   - idle-only switching/replacement and the no-fail publication boundary;
   - local-backend-to-provider-layer source binding;
   - built-in provider identifier ruling and coexistence with `llama-cpp`;
   - stable built-in scoped-model reference encoding and unavailable-reference behavior.
4. Add characterization tests for current TUI startup, print startup/resume, login-required bootstrap, trust cancellation, reload, new/resume, branch provider recovery, provider switching failure, active-run rejection, and cleanup.

Exit criteria:

- preparation/adoption state machine is agreed;
- no unresolved path can execute project extension setup before trust;
- every candidate/outgoing resource has one named owner;
- transcript commit, session-index repair, and no-fail in-memory publication semantics are agreed;
- provider ID and old `llama-cpp` coexistence are decided with tests;
- existing behavior is covered before refactoring.

### Phase 1 — dynamic provider contracts and layered registry

**Goal:** Add provider-neutral registration and refresh mechanics without startup or UI integration.

Likely files:

- new `src/tau_coding/extensions/providers.py`;
- new `src/tau_coding/extensions/provider_registry.py`;
- exports in `src/tau_coding/extensions/__init__.py`;
- registration methods in `extensions/api.py` and `extensions/runtime.py`;
- focused `tests/test_extension_providers.py` or additions to `tests/test_extensions.py`.

Work:

- add `ProviderModel`, `DynamicProvider`, auth strategies, OpenAI transport descriptor, runtime-factory contract;
- add source/generation-aware provider layers;
- add dormant providers;
- accept safe provider-supplied initial snapshots and retain successful snapshots in memory; defer generic disk persistence;
- add async refresh coordinator, shared tasks, timeout, cancellation, diagnostics, and stale publication protection;
- add effective durable baseline composition;
- add complete restoration on unregister/retire;
- ensure failed setup removes provider registrations from that extension source.

Exit criteria:

- all registry behavior is deterministic and frontend-free;
- no dynamic definition is written through durable provider settings;
- stale refreshes cannot publish after re-register, reload, or retire;
- optional/no auth is represented without a fake key.

### Phase 2 — trusted hidden built-in extensions

**Goal:** Add a generic bundled extension source before llama.cpp.

Likely files:

- new `src/tau_coding/built_in_extensions.py`;
- changes to `extensions/loader.py` and `extensions/runtime.py`;
- fake built-in fixture and tests in `tests/test_extensions.py`.

Work:

- define built-in extension declarations;
- load built-ins before discovered extensions;
- preserve hidden/source metadata;
- load despite `--no-extensions`;
- isolate setup failure;
- include built-ins in fresh-generation reload/resume/new behavior;
- prove no project trust prompt is caused by built-ins.

Exit criteria:

- a fake built-in can register a tool, command, and provider; local-backend registration is added and tested in Phase 4;
- failure produces diagnostics without startup failure;
- generation retirement invalidates all captured API and provider work;
- no llama.cpp-specific name appears in generic loader logic.

### Phase 3 — shared trust-aware session/provider preparation

**Goal:** Remove provider-before-extension startup inversion.

Likely files:

- new `src/tau_coding/session_preparation.py`;
- refactor `src/tau_coding/session.py`;
- adapt `src/tau_coding/cli.py`;
- adapt `src/tau_coding/tui/app.py`;
- adapt `src/tau_coding/provider_runtime.py`;
- tests in `test_cli.py`, `test_tui_app.py`, `test_coding_session.py`, `test_project_trust.py`, and provider tests.

Work:

- extract transcript/resource/trust staging from provider construction;
- load effective providers before startup selection;
- unify TUI and print preparation;
- support explicit dynamic `--provider`/`--model`;
- support print resume and TUI resume/new/cwd replacement;
- add async candidate-first model switching;
- unify runtime provider ownership under `CodingSession`;
- enforce idle-only switching/replacement and close replaced providers after the no-fail publication boundary;
- preserve current durable provider migration, HF routing, provider-anchored branch recovery, login-required TUI bootstrap, image support, thinking, and system-prompt controls;
- land provider-aware `ModelChangeEntry` replay before enabling dynamic cross-provider switching;
- make preparation failure close candidate resources and retain the outgoing session.

Exit criteria:

- dynamic provider startup works in TUI and print mode;
- project extension providers never load before trust;
- setup executes once per staged runtime;
- failure leaves no partially switched session/provider metadata;
- all existing startup/resume tests continue to pass.

### Phase 4 — local-backend contract and `/local`

**Goal:** Add the provider-neutral host experience before real llama.cpp behavior.

Likely files:

- new `src/tau_coding/local_backends.py`;
- extension API/runtime registration additions;
- `/local` command in `commands.py`;
- new TUI screen/module under `src/tau_coding/tui/`;
- tests in `test_commands.py`, `test_tui_app.py`, and a focused backend registry test file.

Work:

- define structured configuration fields/transactions and backend status/actions/results/progress;
- add source/generation-aware backend registry;
- add built-in `/local` command action;
- implement zero/one/multiple-backend UI with an explicit chooser and recommended/default marker; one backend is preselected but still confirmed;
- add generic configure, refresh, use, doctor, reset actions;
- integrate async cancellation and stale backend generations;
- validate with two deterministic fake backends with different capabilities.

Exit criteria:

- generic UI contains no llama.cpp-specific protocol vocabulary;
- one or several backends open the same picker; llama.cpp is recommended/preselected while available, but selection is explicit;
- two fake backends can request different normal/secret/choice fields and failed configuration never partially persists;
- backend operations are bound to their own source/provider layer and cannot reset or select a shadowing source;
- absent optional capabilities are not rendered;
- print/headless behavior is explicit and safe;
- a second fake backend proves the contract is not llama.cpp-shaped.

### Phase 5 — built-in llama.cpp connection and inference

**Goal:** Ship useful local inference without router management.

Likely files:

- new `src/tau_coding/extensions/builtins/llama_cpp/` package;
- built-in declaration registration;
- fake HTTP transport fixtures;
- focused `tests/test_llama_cpp_extension.py` plus integration tests.

Work:

- implement endpoint normalization;
- implement versioned non-secret state;
- integrate optional stored/env auth;
- implement reachability/health and `/v1/models` discovery for single-model servers and currently loaded router models;
- register dormant/dynamic provider and local backend;
- implement setup, status, refresh, use, doctor, and reset;
- reuse OpenAI-compatible streaming;
- ensure cached startup and unavailable-server behavior;
- expose real model IDs in `/model`;
- add actionable 401/403, timeout, loading, malformed response, and tool-compatibility messages.

Exit criteria:

- unauthenticated and authenticated llama.cpp servers work without fake values;
- a single-model server works;
- TUI and print explicit startup work after setup;
- server downtime does not break unrelated provider startup;
- no guessed model limits/capabilities are emitted;
- no secret appears in non-secret state or diagnostics.

### Phase 6 — hardening and second-backend validation

**Goal:** Stabilize public generic APIs only after another implementation exercises them.

Work:

- retain the required fake second backend in the permanent test suite;
- perform a small Ollama, vLLM, or LM Studio adapter spike;
- record where the contract was too llama.cpp-specific;
- revise names/capabilities before documenting them as public extension APIs;
- add stress tests for refresh/reload/resume races and resource leaks;
- complete published docs and migration guidance.

Exit criteria:

- second backend requires no llama.cpp concepts;
- API changes from the spike are incorporated before stability claims;
- all #602 required acceptance criteria are met;
- required router-management contracts remain implementable without adding llama.cpp vocabulary to generic APIs;
- Phase 7 remains the final required delivery before issue #602 closes.

### Phase 7 — required llama.cpp router management and scoped models

**Goal:** Complete the Pi-like model-management workflow inside provider-neutral `/local` without changing basic provider semantics.

Work:

- pin official llama.cpp router API behavior and a tested version range;
- capability-detect router mode while preserving single-model fallback;
- implement model-state listing, load, unload, server-side download, and post-operation provider refresh;
- implement Hugging Face GGUF repository search, exact `owner/repository[:quantization]` input, repository details, gated-access guidance, quantization/size selection, and token discovery for search only;
- add progress, cancellation, retry, connection-loss reconciliation, and shared-router confirmation;
- implement stable built-in scoped-model references without persisting dynamic definitions or triggering implicit load/download;
- add fake Hugging Face/router tests, live router validation, failure recovery, and user documentation.

Exit criteria:

- no operation silently downloads, loads, unloads, restores, or deletes anything;
- cancellation and connection loss leave state reconciled through refresh;
- only loaded/sleeping models enter `/model`, and they can be added/removed through `/scoped-models` and the `/model` picker;
- stale/unloaded scoped references never synthesize availability or trigger a router mutation;
- gated repository and token responsibilities are explained without leaking credentials;
- single-model mode remains fully supported;
- all router and Hugging Face behavior stays in the llama.cpp built-in package.

## 15. Deterministic test plan

### 15.1 Provider contracts and registry

Test:

- valid provider with zero models;
- invalid empty IDs/names;
- duplicate model IDs;
- invalid default model;
- transport/factory validation;
- durable baseline overridden and completely restored;
- two extension layers with deterministic precedence;
- same-source atomic replacement;
- invalid replacement preserves old layer;
- unregister unknown source is harmless;
- setup failure removes source registrations;
- generation retirement removes providers/backends;
- cached snapshot restoration;
- failed refresh retains current/cache;
- timeout retains current/cache;
- cancelled refresh does not publish;
- concurrent refresh callers share one task;
- re-registration invalidates old refresh;
- old-generation completion cannot overwrite new generation;
- diagnostics are bounded and source-aware;
- required/optional/no auth resolution;
- absent optional key omits `Authorization`;
- present key adds `Authorization`;
- secret values never appear in repr/diagnostics/snapshot serialization.

### 15.2 Built-in extensions

Test:

- built-in setup runs once per staged runtime;
- built-in loads with `--no-extensions`;
- built-in loads before user/explicit/project sources;
- hidden metadata affects listing but not diagnostics;
- setup exception is isolated;
- built-in does not trigger project trust;
- reload creates a fresh generation;
- resume/new/cwd replacement does not reuse the old generation;
- retired built-in tasks are cancelled.

### 15.3 Preparation and trust

Test TUI and print paths for:

- static durable startup unchanged;
- explicit dynamic provider/model startup;
- dynamic provider dormant then refreshed;
- explicit missing model fails without fallback;
- implicit startup ignores unavailable local backend;
- cached local provider starts without network;
- print-mode resume of a dynamic provider;
- explicit override of a resumed dynamic session;
- provider migration still runs correctly;
- HF inference-provider pin is not applied to non-HF providers;
- project provider unavailable before approval;
- approved project provider available after setup;
- decline excludes project provider but retains built-ins/user/explicit sources;
- trust cancellation preserves current session;
- project cwd replacement rebuilds provider registry;
- setup runs once, not once in frontend and again in session load;
- candidate runtime creation failure preserves active runtime/model;
- atomic `ModelChangeEntry` + `LeafEntry` batch failure leaves neither an orphan entry nor a switched harness; repairable index failure leaves a committed provider-aware switch plus a diagnostic;
- atomic startup/replacement batches cover initialization and staged repair entries; failure leaves the prior file unchanged and crash-after-commit resumes the committed leaf;
- concurrent normal append and batch replacement under the new cross-process session lock cannot lose entries; temp-file, file, and directory durability paths are tested;
- resume after a committed switch plus failed index update follows transcript provider/model and repairs the stale index;
- switching/reset/reload/resume/new is rejected during an active run without closing the in-flight provider;
- candidate abort closes provider and cancels tasks;
- successful switch closes replaced Tau-owned provider immediately;
- final shutdown closes each provider once.

### 15.4 `/local`

Test:

- command is registered and appears in autocomplete/reference list;
- no backend state;
- one backend opens the picker with it preselected and requires confirmation;
- multiple-backend picker marks llama.cpp recommended/default;
- unavailable backend retry/close;
- dormant backend;
- cached/stale status;
- configure cancellation;
- secret input is not echoed or stored in backend state;
- refresh cancellation/timeout;
- doctor stage rendering;
- model use invokes async candidate-first switch;
- reset confirmation, idle requirement, fallback selection/cancellation, active-model safety, source-layer isolation, and credential-deletion partial failure;
- different normal/secret/choice configuration fields across two fake backends;
- configuration validation/cancellation leaves settings and credentials unchanged;
- configuration write failure before safe-state commit leaves only a detectable unreferenced generation credential; post-commit old-credential cleanup failure leaves the new configuration usable and reports cleanup;
- unsupported router actions remain hidden;
- fake second backend with a different capability set;
- generic host renders backend model-management progress/actions without llama.cpp, GGUF, router, quantization, or Hugging Face vocabulary.

### 15.5 llama.cpp fake HTTP server

Use `httpx2.MockTransport` or the transport injection already used by Tau. Cover:

- root URL normalization;
- `/v1` normalization;
- trailing slash normalization;
- invalid scheme/userinfo/query/fragment;
- connection refused;
- timeout;
- loading/unavailable health state;
- 401 and 403 guidance;
- no-auth request header omission;
- generation-referenced stored/env key header inclusion and precedence;
- orphan generation credential cleanup and reset of only the referenced credential;
- successful `/v1/models` discovery;
- one-model automatic selection;
- multiple-model selection;
- empty model list;
- duplicate IDs;
- malformed JSON and malformed model objects;
- server-reported safe metadata;
- absent metadata remains unknown while Tau's operational fallback is labeled as fallback;
- model IDs that resemble first-party OpenAI/Codex names still use the explicitly selected local API endpoint;
- cached snapshot after failed refresh;
- active model disappears after refresh;
- streaming completion doctor probe;
- tool schema accepted;
- tool call emitted;
- streaming works but tool call is inconclusive;
- state file round trip, permissions, locking, atomic replace, and schema version;
- endpoint precedence, environment non-persistence, and cache invalidation on endpoint change;
- reset removes state but not credential without separate confirmation;
- real `ExtensionRuntime` loading rather than only direct module imports;
- router `/models`, `/models/load`, `/models/unload`, `/models/sse`, and download response parsing against the pinned contract;
- loaded, sleeping, unloaded, loading, downloading, failed, and unknown states;
- load/unload/download success, failure, cancellation, timeout, connection loss, retry, and post-operation reconciliation;
- no silent unload when loading with other models active; keep/unload/cancel choices and approved restoration behavior;
- fake Hugging Face GGUF search, exact repository input, details, quantization extraction, sizes, rate limiting, anonymous/token auth, and gated repositories;
- Hugging Face and llama.cpp credentials absent from logs, state, diagnostics, sessions, and exports;
- loaded models become `/model` choices and safe scoped references; unloaded/stale references remain inert.

### 15.6 Regression suites

Focused files likely include:

```text
tests/test_extensions.py
tests/test_project_trust.py
tests/test_provider_config.py
tests/test_provider_runtime.py
tests/test_coding_session.py
tests/test_session.py
tests/test_session_manager.py
tests/test_cli.py
tests/test_tui_app.py
tests/test_commands.py
tests/test_cross_provider_history.py
tests/test_http.py
tests/test_tau_ai.py
```

Run per PR:

```bash
uv run pytest <focused test files>
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Run before each user-facing merge and final closure:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
hugo --source website --minify
uv build
```

Also run the repository's GitHub Python and documentation checks when available.

## 16. Manual validation

### 16.1 Single-model unauthenticated server

```bash
llama-server -hf <tool-capable-gguf>
tau
```

Validate:

```text
/local
/model
```

Then run a coding tool turn and verify no `Authorization` header is required.

### 16.2 API-key server

Start llama.cpp with `--api-key`, configure through `/local`, and validate:

- wrong/missing key guidance;
- stored credential use;
- `LLAMA_API_KEY` fallback;
- no key in state/session/diagnostics/export.

### 16.3 Print mode and resume

```bash
tau --provider llama.cpp --model <id> -p "summarize this project"
tau --print --session <session-id> "continue"
```

Validate cached startup while the discovery endpoint is temporarily unavailable.

### 16.4 Multiple models and metadata

Validate:

- several returned model IDs;
- model picker display names;
- one selected model disappearing;
- unknown context/output/modality fields staying unknown;
- reliable positive server metadata when present.

### 16.5 Tool compatibility

Run doctor and a real coding turn:

```text
Use the bash tool to run pwd and report the result.
```

Validate a tool-capable and non-tool-capable model. The latter should receive a useful warning, not a generic connectivity failure.

### 16.6 Reload and replacement

Validate:

- `/reload`;
- `/new`;
- `/resume` in the same cwd;
- resume into a different cwd with trust approval;
- trust decline/cancel;
- server failure during replacement;
- no duplicate registrations/tasks;
- old provider connections closed.

### 16.7 Router discovery and management

Before closing Phase 6, run a live current llama.cpp router with at least one loaded model and verify standard `/v1/models` discovery, `/model` selection, and inference. Before closing required Phase 7 and #602, additionally validate:

- no models loaded;
- one model load/unload;
- several loaded models;
- load while another model is loaded;
- cancelled load/download;
- connection loss and retry;
- gated Hugging Face repository;
- shared-router state changed by another client;
- exact `owner/repository[:quantization]` input and GGUF search/quantization selection;
- anonymous and authenticated Hugging Face search, including a gated repository;
- loaded model added and removed through `/scoped-models` and the `/model` picker;
- scoped model later unloaded or absent without implicit reload;
- single-model mode still unaffected.

## 17. Documentation deliverables

Update installed docs:

- `src/tau_coding/data/docs/extensions.md` — provider/local-backend APIs and built-in source semantics;
- `src/tau_coding/data/docs/models.md` — `/local`, optional/no auth, dynamic versus durable providers;
- `src/tau_coding/data/docs/cli.md` — explicit local provider startup behavior;
- `src/tau_coding/data/docs/tui.md` — `/local` UI;
- `src/tau_coding/data/docs/security.md` — built-in trust and project-provider boundaries;
- `src/tau_coding/data/docs/architecture.md` — package ownership and preparation boundary.

Update published docs:

- `website/content/guides/extensions.md`;
- `website/content/guides/providers-and-models.md`;
- new or expanded local-inference/llama.cpp guide;
- `website/content/reference/slash-commands.md`;
- `website/content/reference/cli.md`;
- `website/content/reference/configuration.md`;
- `website/content/guides/project-trust.md`;
- `website/content/guides/tui.md`;
- `website/content/internals/architecture.md`.

Add beginner-friendly development notes:

- preparation lifecycle ADR;
- dynamic provider registry and overlay semantics;
- trusted built-in extension design;
- local-backend contract;
- built-in llama.cpp implementation and Pi comparison;
- llama.cpp router management, Hugging Face search/download, scoped references, and Pi comparison.

Documentation must remove or clearly supersede the current fake-key/fake-model llama.cpp quickstart. Keep the static custom-provider path documented for other endpoints.

## 18. Pi alignment and intentional differences

### 18.1 Follow Pi

- hidden built-in llama.cpp extension;
- provider object registration;
- dormant/cached dynamic models;
- provider registration before startup selection;
- extension-owned protocol and management behavior;
- router model-state listing, explicit load/unload, Hugging Face GGUF search/download, progress, cancellation, and shared-router safeguards;
- normal model picker and streaming after registration;
- model refresh after management changes;
- isolated extension failures;
- source invalidation on reload/session replacement.

Reference files at the pinned Pi baseline:

```text
packages/coding-agent/src/core/extensions/types.ts
packages/coding-agent/src/core/model-registry.ts
packages/coding-agent/src/extensions/index.ts
packages/coding-agent/src/extensions/llama/provider.ts
packages/coding-agent/src/extensions/llama/index.ts
packages/coding-agent/docs/llama-cpp.md
```

### 18.2 Deliberately differ

- `/local`, not `/llama`;
- generic multi-backend local UI;
- basic single-model server support without router mode;
- no fake `local` API key;
- no 128K fallback or `maxTokens=contextWindow` guess;
- no silent unload;
- one provider-neutral `/local` wizard combines backend selection, connection setup, and management instead of Pi's separate `/login llama.cpp` and `/llama` entry points;
- current Tau project-trust and cwd-bound staged runtime semantics;
- Python typed dataclasses/protocols rather than copying TypeScript shapes literally;
- process-local overlays separate from Tau's durable provider catalog.

Record any additional deviations as explicit rulings in the implementation architecture note.

## 19. Risks and mitigations

### Startup/session refactor regression

**Risk:** Shared preparation touches TUI, print, resume, trust, and provider selection.
**Mitigation:** Phase 0 characterization tests; extract stages without changing behavior first; migrate one frontend at a time behind the same service.

### Project trust bypass

**Risk:** Provider registration or refresh executes project code before approval.
**Mitigation:** Runtime-local registries; load/restore project source only after trust; test setup and network callbacks separately.

### Stale async publication

**Risk:** Old refresh overwrites new endpoint/models after reload.
**Mitigation:** Source + layer + generation publication token; cancellation; shared-task bookkeeping under one coordinator.

### Provider/session partial switch

**Risk:** Active model changes before runtime creation succeeds.
**Mitigation:** Async candidate-first switch and one atomic adoption point.

### Resource leaks or double close

**Risk:** Frontend and session both own startup provider, or replacement loses ownership.
**Mitigation:** Prepared-session ownership ledger; idempotent abort; session owns adopted providers; explicit close-count tests.

### Durable configuration contamination

**Risk:** A dynamic provider definition ends up in the catalog/global defaults, or scoped-model persistence accidentally serializes transient definitions or metadata.
**Mitigation:** Separate effective-provider view and persistence guards; no conversion back to `ProviderSettings` for saves. Scoped persistence is restricted to host-defined stable references for trusted built-ins and never stores provider definitions, endpoint data, credentials, or discovered metadata.

### Secret leakage

**Risk:** API key enters extension state, snapshots, diagnostics, URLs, or session export.
**Mitigation:** host secret input; credential-store-only persistence; reject URL userinfo; allowlisted snapshot schema; redaction tests.

### Generic API shaped only around llama.cpp

**Risk:** `/local` becomes `/llama` with another name.
**Mitigation:** fake second backend in Phase 4 and a real adapter spike before API stability.

### Unknown metadata converted into unsafe defaults

**Risk:** Compaction, image handling, or request limits use guessed values.
**Mitigation:** optional metadata through all layers; conservative existing fallback only where Tau already requires it; source shown in `/session`.

### Built-in integration blocks normal startup

**Risk:** Local network failure slows or crashes every Tau launch.
**Mitigation:** synchronous cached registration, no unrelated startup probing, explicit bounded refresh, dormant provider, isolated diagnostics.

### Existing custom llama.cpp configuration conflicts

**Risk:** Existing `llama-cpp` user provider changes meaning.
**Mitigation:** canonical built-in ID `llama.cpp`; no automatic migration; document coexistence and optional manual cleanup.

### Public API churn

**Risk:** External extensions adopt an unvalidated contract.
**Mitigation:** mark APIs provisional through second-backend validation; document stability only after Phase 6.

## 20. Non-goals

- installing or compiling llama.cpp;
- starting or stopping `llama-server`;
- configuring CUDA, Metal, ROCm, or other acceleration;
- quantizing models inside Tau;
- silently downloading/loading/unloading/deleting models;
- requiring router mode;
- adding `/llama` or `/llama-cpp`;
- putting extension or local-provider logic in `tau_agent`;
- putting llama.cpp protocol branches in generic session/TUI code;
- persisting plaintext secrets;
- guessing model context, output, modality, reasoning, or tool support;
- automatically making llama.cpp the global provider;
- probing every local backend at ordinary startup;
- shipping every local backend in the first release;
- stabilizing provider-contributed `/login` in this work;
- adding a top-level `tau local` CLI without a separate automation requirement.

## 21. Decisions to confirm during Phase 0

These are bounded implementation choices, not reasons to delay decomposition:

1. Final provider ID after validating `llama.cpp` across CLI, session, catalog coexistence, and diagnostics.
2. Final module names for provider registry and session preparation.
3. Exact state path under `TauPaths`; preferred shape is `~/.tau/state/extensions/llama.cpp.json`.
4. Named refresh timeout defaults for startup, `/model`, and `/local`.
5. Exact unavailable behavior for an explicitly resumed local session with no cache.
6. Which real second backend receives the Phase 6 spike.
7. Exact stable-reference encoding and unavailable rendering for scoped llama.cpp models.
8. Whether built-in extensions appear in `/session` counts or only detailed diagnostics.

Every decision must be recorded in the architecture note and tested. Do not invent provider metadata or security behavior to resolve convenience questions.

## 22. Definition of done

Issue #602 is complete only when all required phases through Phase 7 satisfy these conditions:

### Architecture

- [ ] llama.cpp ships as a trusted hidden built-in extension.
- [ ] `tau_agent` remains provider- and extension-agnostic.
- [ ] Built-ins use generic extension/provider/backend registration APIs.
- [ ] Dynamic provider composition follows current trust-aware fresh-runtime staging.
- [ ] TUI and print mode use one preparation path.
- [ ] Dynamic definitions remain separate from durable catalog definitions.

### Provider lifecycle

- [ ] Providers can register with zero models.
- [ ] Provider-supplied initial snapshots and the endpoint-keyed llama.cpp cache restore before optional network refresh.
- [ ] Refresh is async, cancellable, timeout-aware, coalesced, atomic, and generation-safe.
- [ ] Invalid registration/refresh preserves the last working layer/snapshot.
- [ ] Unregister/retire restores the preceding dynamic or durable definition.
- [ ] Runtime creation or atomic authoritative entry-batch failure preserves the active session and destination transcript.
- [ ] New model-change history records provider identity and branch/resume replay uses it.
- [ ] Switching and replacement reject active runs without closing in-flight providers.
- [ ] Replaced/aborted resources close exactly once.
- [ ] Startup and replacement share one tested durable-commit/no-fail-publication protocol.

### User experience

- [ ] `/local` exists with no `/llama` alias.
- [ ] `/local` presents a backend chooser and marks llama.cpp recommended/preselected without silently selecting Tau's global provider.
- [ ] Endpoint and optional API key setup works, probing only the configured/default endpoint rather than scanning processes, ports, or networks.
- [ ] An unavailable local server does not block unrelated Tau startup.
- [ ] TUI login-required startup remains available when no ordinary provider is usable; print mode fails safely.
- [ ] Loaded discovered models appear in `/model` with real IDs; unloaded router models appear only in `/local` management.
- [ ] Compatible routers support explicit Hugging Face GGUF search/download and model load/unload with progress, cancellation, retry, and shared-router confirmation.
- [ ] Loaded llama.cpp models can be managed through scoped-model controls using safe stable references; stale/unloaded references never trigger implicit management.
- [ ] TUI and print explicit selection work after setup.
- [ ] Resume, reload, new session, and cwd replacement preserve correct behavior.
- [ ] Doctor distinguishes connectivity, auth, streaming, schema, and tool-call behavior.
- [ ] Optional/no auth emits no authorization header without a key.
- [ ] Unknown metadata remains unknown, and Tau operational fallbacks are labeled as fallbacks rather than server claims.

### Extensibility

- [ ] `/local` consumes a provider-neutral backend contract with structured, transactional normal/secret/choice configuration fields.
- [ ] Backends are bound to their source-owned provider layer and cannot mutate or select a shadowing source.
- [ ] llama.cpp protocol logic remains in its built-in package.
- [ ] A fake second backend is permanently tested.
- [ ] A real second-backend spike informs the public contract or is documented with a concrete reason for deferral.
- [ ] External extensions have a supported future path to the same provider/backend APIs.

### Security and persistence

- [ ] Project extensions never execute before trust and explicit project-extension opt-in.
- [ ] Built-in failures are diagnostics, not host crashes.
- [ ] Secrets never enter non-secret state, snapshots, sessions, exports, or diagnostics.
- [ ] Existing provider schema migration and user catalog behavior remain intact.
- [ ] Existing `llama-cpp` custom providers remain valid and distinct under the final provider-ID ruling.

### Quality and documentation

- [ ] Deterministic tests cover the complete lifecycle and failure matrix.
- [ ] Live unauthenticated and authenticated llama.cpp smoke tests pass.
- [ ] Full pytest, Ruff, formatting, mypy, Hugo, package build, and GitHub checks pass.
- [ ] Installed and published docs describe `/local`, llama.cpp, security, configuration, and troubleshooting.
- [ ] Beginner-friendly architecture notes explain the design and Pi comparison.
- [ ] Required router management, Hugging Face search/download, and scoped-model integration are delivered and documented without regressing single-model inference.

## 23. References

Tau:

- issue #602;
- closed issues #415 and #443;
- closed draft PR #417;
- `AGENTS.md`;
- `src/tau_coding/data/docs/{extensions,models,cli,security,tui,architecture}.md`;
- `website/content/guides/extensions.md`;
- `website/content/guides/providers-and-models.md`;
- `website/content/guides/project-trust.md`;
- `website/content/guides/tui.md`;
- `website/content/reference/{cli,slash-commands,configuration}.md`;
- `website/content/internals/architecture.md`;
- `dev-notes/architecture/phase-21-extensions.md`.

Prototype evidence, not current-main implementation authority:

- `feat/llama-cpp-extension-api:dev-notes/architecture/extension-providers.md`;
- `feat/llama-cpp-first-class:dev-notes/architecture/llama-cpp-first-class.md`.

Pi at `eb1f87fa9a29e27e0c63dcb40dbed9a3624c82b1`:

- `packages/coding-agent/src/core/extensions/types.ts`;
- `packages/coding-agent/src/core/model-registry.ts`;
- `packages/coding-agent/src/extensions/index.ts`;
- `packages/coding-agent/src/extensions/llama/provider.ts`;
- `packages/coding-agent/src/extensions/llama/index.ts`;
- `packages/coding-agent/docs/llama-cpp.md`.
