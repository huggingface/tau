# Issue 546: expose API protocol selection for custom OpenAI-compatible providers

Issue: https://github.com/huggingface/tau/issues/546

Pi reference: https://github.com/badlogic/pi-mono

## Goal

Implement custom-provider API selection as a direct port of Pi's API-selection
model. Make catalog metadata authoritative throughout Tau's application path,
remove model-name routing from the OpenAI-compatible adapter, and do not add a
durable `infer_api_from_model` or equivalent Tau-only configuration concept.

## Intuition

Tau already implements both OpenAI wire protocols. The bug is that the model
name can currently override catalog intent:

```text
Current Tau

catalog api ─────────────┐
                        ├──► model-name heuristic ──► endpoint
model id ────────────────┘
```

The Pi-faithful flow is simpler:

```text
Desired Tau, matching Pi

provider api ────────────┐
                        ├──► resolved model api ──► exact transport
model-level api ─────────┘

model id ───────────────────► request payload only
```

For the issue's two counterexamples:

```text
company/openai/gpt-5.6 + openai-responses
    └──► POST /responses

gpt-5.5-proxy + openai-completions
    └──► POST /chat/completions
```

Neither result depends on the spelling of the model ID.

## Pi reference behavior

Pi makes `api` part of the resolved model. It resolves a custom model's API in
this order:

```text
model.api  ??  provider.api  ??  inherited model api
```

It then dispatches through the provider registered for that exact `model.api`.
The model ID never selects an API protocol.

Use these upstream sources as the normative references, and record the Pi
revision used during implementation in the development note:

- `packages/ai/src/types.ts`: Pi's resolved `Model` has a required `api` field.
- `packages/coding-agent/src/core/model-config.ts`: `api` is accepted at both
  provider and custom-model level.
- `packages/coding-agent/src/core/provider-composer.ts`: custom model
  composition resolves `definition.api ?? providerConfig.api ?? defaults?.api`
  and errors if no API can be resolved.
- `packages/coding-agent/src/core/provider-composer.ts`: streaming dispatches
  with `getApiProvider(model.api)`.
- `packages/coding-agent/docs/models.md`: documents provider-level defaults and
  model-level API overrides, including `openai-completions` and
  `openai-responses`.

Canonical links:

- https://github.com/badlogic/pi-mono/blob/main/packages/ai/src/types.ts
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/src/core/model-config.ts
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/src/core/provider-composer.ts
- https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/models.md

### Pi to Tau mapping

| Pi concept | Tau equivalent | Required behavior |
| --- | --- | --- |
| `Model.api` | `provider_api(provider, model)` and `OpenAICompatibleConfig.api` | One concrete protocol is resolved before streaming |
| Provider `api` | `ProviderCatalogEntry.api` / `OpenAICompatibleProviderConfig.api` | Default API for the provider's models |
| Custom-model `api` | `ModelCatalogMetadata.api` / `ProviderModelMetadata.api` | Overrides the provider API for that model |
| `model.api ?? provider.api ?? default` | `provider_api()` plus the provider-kind compatibility default | Model metadata wins, then provider metadata |
| `getApiProvider(model.api)` | `create_model_provider()` plus exact branching inside `OpenAICompatibleProvider` | Dispatch by API metadata, never by model ID |
| `models.json` | `~/.tau/catalog.toml` | Durable provider/model capability metadata |

Tau uses one Python `OpenAICompatibleProvider` class with two internal
transports, whereas Pi registers separate API implementations. That is an
acceptable language-level difference only if dispatch remains behaviorally
equivalent: the resolved `api` value alone chooses the transport.

## Architectural fit

Tau's dependency direction remains:

```text
tau_coding  ─────►  tau_agent  ─────►  tau_ai
 application        portable brain     provider streams
```

For this feature:

```text
tau_coding
├── CLI `tau setup`
├── TUI `/login custom`
├── catalog composition
└── resolved runtime configuration
          │
          ▼
tau_ai.OpenAICompatibleProvider
├── api == openai-completions ──► /chat/completions
└── api == openai-responses ────► /responses
```

No change belongs in `tau_agent`, `AgentHarness`, the agent loop, tools, event
types, or session storage. Pi also hands its agent core an already resolved
model/provider; provider configuration and composition stay in the coding
application and AI-provider layers.

The custom setup screens are Tau-specific frontend conveniences—Pi primarily
uses `models.json` for this configuration—but they must produce the same
provider/model metadata Pi would consume. UI differences are acceptable;
configuration and dispatch semantics are not.

## Detailed implementation plan

### 1. Make the API value the sole routing authority

Update `src/tau_ai/openai_compatible.py` so `_stream_provider_events()` selects
the endpoint only from `self._config.api`:

```python
if self._config.api == "openai-responses":
    return self._stream_responses(...)
return self._stream_chat_completions(...)
```

Remove the Tau-specific routing policy:

- `_RESPONSES_ONLY_PREFIXES`
- `_use_responses_api()`
- the `"codex"` substring check
- the model-prefix branch inside `_stream_provider_events()`

Update `src/tau_ai/env.py` to remove `infer_api_from_model` from
`OpenAICompatibleConfig`. Once routing matches Pi, there is nothing left to
infer.

Also remove now-obsolete `infer_api_from_model=False` arguments and assertions
from:

- `src/tau_coding/provider_runtime.py`
- `src/tau_coding/extensions/builtins/llama_cpp/service.py`
- `tests/test_llama_cpp_extension.py`

Dynamic providers and llama.cpp remain deterministic because their configured
`api="openai-completions"` directly selects Chat Completions.

### 2. Centralize Pi's provider/model precedence in Tau's provider composition

Tau already has the right durable-provider resolution logic in the private
`src/tau_coding/provider_config.py::_provider_api()` helper. Promote it to a
module-level application helper named `provider_api()` and use it everywhere
that needs the resolved API:

```python
def provider_api(provider: ProviderConfig, model: str | None = None):
    metadata = _metadata_for_model(provider, selected_model)
    if metadata is not None and metadata.api is not None:
        return metadata.api
    return provider.api
```

Preserve and test this mapping explicitly:

```text
ProviderCatalogEntry.api
        │
        ├───────────────┐
        ▼               │
provider.api            │
                        ▼
model_metadata.api ──► resolved api ──► OpenAICompatibleConfig.api
     (override)
```

Keep this resolver in `tau_coding`: protocol composition is application/model
metadata policy. Do not move provider catalog knowledge into `tau_ai` or
`tau_agent`. Leave the existing session and RPC projection logic unchanged;
persisted provider/model metadata already uses the same precedence there, and
refactoring extension runtime fallbacks is outside issue #546.

Do not add `infer_api_from_model` or an `api_was_explicit` field to
`OpenAICompatibleProviderConfig`; neither has a Pi counterpart.

For legacy Tau catalog entries that omit provider and model `api`, keep the
existing provider-kind compatibility default of `openai-completions`. This is a
deterministic configuration migration fallback, not runtime model-name
inference:

```text
missing api on legacy openai-compatible entry
    └──► openai-completions
```

This fallback should be documented. It should not inspect the model ID, and it
should not silently choose Responses. A later explicit save may materialize the
resolved provider API in `catalog.toml`, matching Pi's documented requirement
that custom models resolve an API from provider or model metadata.

Preserve Tau's existing persistence split:

```text
catalog.toml   = provider/model capabilities, including api
providers.json = user preferences and provider ordering; no api ownership
session JSONL  = selected provider/model and messages; no new api field
```

Verify catalog load, merge, save, and reload rather than introducing another
configuration location. In particular, account for
`_provider_definition_differs_from_catalog()`: the TUI must create its initial
`ProviderCatalogEntry` with `api` populated because a pre-existing entry whose
API is absent is intentionally not treated as an override during settings
merge/save.

### 3. Add protocol selection to `tau setup`

Update `src/tau_coding/cli.py`:

- Add an `api` argument to `setup_command()`.
- Add a `--api` option to the root callback, scoped in help text to `tau setup`.
- Accept the exact Pi/Tau protocol identifiers:

  ```text
  openai-completions
  openai-responses
  ```

- Default to `openai-completions`, Pi's documented choice for most compatible
  custom servers.
- Pass the selected value into `OpenAICompatibleProviderConfig.api`.
- Let the existing catalog serialization write the same value to the provider's
  `api` field.

Example:

```bash
tau --provider company \
  --base-url https://ai.company.example/v1 \
  --model company/openai/gpt-5.6 \
  --api openai-responses \
  setup
```

Invalid values should fail as CLI usage errors before any files are written.

The option and persisted values intentionally use Pi's API names rather than a
new Tau-specific enum or aliases.

### 4. Add protocol selection to `/login custom`

Update `src/tau_coding/tui/app.py`:

```python
@dataclass(frozen=True, slots=True)
class CustomProviderLoginResult:
    ...
    api: Literal["openai-completions", "openai-responses"]
```

In `CustomProviderLoginScreen`:

- Add a Textual `Select` after the base URL.
- Present friendly labels while retaining the exact Pi protocol values:

  ```text
  OpenAI Chat Completions (/chat/completions)
  OpenAI Responses (/responses)
  ```

- Default to Chat Completions.
- Follow the existing mixed `Input`/`Select` form pattern in
  `src/tau_coding/tui/local_backends.py::LocalConfigureScreen`: query the next
  field without assuming it is an `Input`, and use `cast(Select[str],
  widget).value` when collecting the choice.
- Rename `_INPUT_ORDER` to a field-oriented name and insert the selector after
  `custom-provider-base-url`. Submitting the base-URL input must focus the
  selector, and leaving the selector must continue to the API-key-environment
  field without breaking keyboard-only entry.
- Advance after the user accepts either protocol, including accepting the
  unchanged default with Enter. Do not let the selector's mount-time default
  event move focus.
- Validate the selection when collecting the result.
- Extend the existing custom-provider CSS so the selector matches the form.

When saving, put the value into both representations:

```python
provider = OpenAICompatibleProviderConfig(
    ...,
    api=result.api,
)

catalog_entry = ProviderCatalogEntry(
    ...,
    api=result.api,
)
```

Writing the API directly into `ProviderCatalogEntry` ensures
`~/.tau/catalog.toml` remains the capability source of truth, analogous to Pi's
`models.json`; `providers.json` remains preferences-only.

### 5. Add Pi-parity regression tests

#### API dispatch contract

Replace the heuristic-oriented tests in `tests/test_tau_ai.py` with a table that
proves model names are irrelevant:

| Configured API | Model ID | Expected endpoint |
| --- | --- | --- |
| `openai-completions` | `gpt-5.5-proxy` | `/chat/completions` |
| `openai-completions` | `company/openai/gpt-5.6` | `/chat/completions` |
| `openai-responses` | `gpt-4o` | `/responses` |
| `openai-responses` | `company/openai/gpt-5.6` | `/responses` |

The important invariant is:

```python
endpoint = endpoint_for(config.api)
# never endpoint_for(model_id)
```

Keep the existing request-shape and SSE parser tests for both transports; only
make their API selection explicit in every `OpenAICompatibleConfig`
construction. Add a direct environment-config regression proving that an
unset API remains `openai-completions`.

#### Provider/model composition contract

Extend `tests/test_provider_config.py`, `tests/test_provider_catalog.py`, and
`tests/test_provider_runtime.py`:

- Provider-level `openai-completions` reaches the completions runtime path.
- Provider-level `openai-responses` reaches the Responses runtime path.
- Model-level `api` overrides the provider-level default.
- A legacy OpenAI-compatible catalog entry with no `api` resolves
  deterministically to `openai-completions`.
- Misleading model IDs do not alter any of those results.
- The final assistant message records the resolved API, matching Pi's model API
  metadata rather than an inferred transport.
- Catalog serialization round-trips provider-level and model-level APIs, while
  provider preferences remain API-free.

Name or document the precedence test as a Pi-parity contract so future changes
do not reintroduce model-name routing.

#### CLI setup

Extend `tests/test_cli.py`:

- `--api openai-responses` appears in the saved catalog and loaded provider.
- Explicit/default Chat Completions is saved as `openai-completions`.
- Unsupported API values fail without writing configuration.
- Existing timeout, retry, default-provider, and missing-key behavior remains
  intact.

#### TUI setup

Extend `tests/test_tui_app.py`:

- The custom-provider screen exposes both friendly protocol choices.
- The chosen exact API value reaches `CustomProviderLoginResult`.
- Enter/focus traversal crosses the new `Select` and continues to the next
  field; keyboard-only submission still saves.
- Saving includes `api = "openai-responses"` or
  `api = "openai-completions"` in `catalog.toml`.
- Existing credential, reload, and provider-selection behavior remains
  unchanged.

#### Dynamic provider regression

Update `tests/test_extension_providers.py` and
`tests/test_llama_cpp_extension.py` as needed to prove that explicit
`openai-completions` still routes misleading names such as `gpt-5.4-local` to
Chat Completions after the inference flag is removed.

### 6. Document the Pi mapping and compatibility decision

Update:

- `website/content/guides/providers-and-models.md` with `tau setup --api` and
  `/login custom` behavior.
- `website/content/reference/configuration.md` with provider-level defaults,
  model-level overrides, and exact supported API names.
- `src/tau_coding/data/docs/models.md` so Tau's bundled self-documentation
  explains the same model as Pi's `docs/models.md`.
- `src/tau_coding/data/docs/cli.md` with the `tau setup` option.
- `src/tau_ai/openai_compatible.py` module/class documentation so it no longer
  claims model names select the Responses API.
- Add `dev-notes/openai-compatible-protocol-selection.md` with:
  - the upstream Pi revision used;
  - the Pi-to-Tau mapping table;
  - why model-name inference was removed;
  - the deterministic legacy fallback to Chat Completions;
  - test and manual verification instructions.

Suggested catalog example:

```toml
[[providers]]
name = "company-ai"
display_name = "Company AI"
kind = "openai-compatible"
base_url = "https://ai.company.example/v1"
api = "openai-responses"
models = ["company/openai/gpt-5.6"]
default_model = "company/openai/gpt-5.6"
```

Also document the override form that corresponds to Pi's per-model `api`:

```toml
api = "openai-completions"

[providers.model_metadata."responses-only-model"]
api = "openai-responses"
```

## Compatibility policy

The Pi-faithful behavior intentionally changes Tau's old heuristic:

| Existing Tau configuration | Revised behavior |
| --- | --- |
| Explicit `api = "openai-completions"` | Always Chat Completions |
| Explicit `api = "openai-responses"` | Always Responses |
| Model metadata contains `api` | Model API overrides provider API |
| No API anywhere on an OpenAI-compatible entry | Deterministic legacy default: Chat Completions |

An old API-less custom provider that relied on a `gpt-5.4`, `gpt-5.5`, or
`*codex*` name to reach Responses will need `api = "openai-responses"` added.
That is preferable to preserving an undocumented inference mechanism that can
also route proxy model IDs incorrectly and has no Pi equivalent.

Call this out in the release note or migration documentation. Do not attempt to
guess and rewrite old configurations from their model names.

## Validation

Run focused checks first:

```bash
uv run pytest \
  tests/test_tau_ai.py \
  tests/test_provider_config.py \
  tests/test_provider_catalog.py \
  tests/test_provider_runtime.py \
  tests/test_coding_session.py \
  tests/test_rpc.py \
  tests/test_extension_providers.py \
  tests/test_llama_cpp_extension.py \
  tests/test_cli.py \
  tests/test_tui_app.py
```

Then the repository's standard suite:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

If published documentation changes, also build the Hugo website.

Manual acceptance checks:

```text
1. Configure company/openai/gpt-5.6 with openai-responses.
   Expected: POST <base-url>/responses.

2. Configure gpt-5.5-proxy with openai-completions.
   Expected: POST <base-url>/chat/completions.

3. Configure one provider with a completions default and a model-level
   responses override.
   Expected: each model uses its resolved metadata API, matching Pi.
```

## Expected change surface

No new provider implementation is needed, but the existing combined adapter's
selection policy changes to match Pi.

```text
Updated
├── src/tau_ai/env.py
├── src/tau_ai/openai_compatible.py
├── src/tau_coding/cli.py
├── src/tau_coding/tui/app.py
├── src/tau_coding/provider_config.py
├── src/tau_coding/provider_runtime.py
├── src/tau_coding/extensions/builtins/llama_cpp/service.py
├── tests/test_tau_ai.py
├── tests/test_provider_config.py
├── tests/test_provider_catalog.py
├── tests/test_provider_runtime.py
├── tests/test_llama_cpp_extension.py
├── tests/test_cli.py
├── tests/test_tui_app.py
├── website/content/guides/providers-and-models.md
├── website/content/reference/configuration.md
├── src/tau_coding/data/docs/models.md
└── src/tau_coding/data/docs/cli.md

Added
└── dev-notes/openai-compatible-protocol-selection.md

Unchanged
├── src/tau_agent/*
├── agent/session event formats
├── Chat Completions request/parser implementation
└── Responses request/parser implementation
```
