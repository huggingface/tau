# Issue 546 custom-provider API selection

Issue [#546](https://github.com/huggingface/tau/issues/546)

Pi reference [badlogic/pi-mono](https://github.com/badlogic/pi-mono)

## Goal

Give users a clear way to choose the OpenAI API supported by a custom provider.
The choice must be available through `tau setup`, `/login custom`, and catalog
metadata. Tau must use the saved value consistently for every model request.

The implementation should follow Pi's resolved-model API behavior and preserve
Tau's existing package boundaries.

## Intuition

Tau already supports Chat Completions and Responses. Custom-provider setup
previously collected a URL and model without asking which API the server uses.
The provider adapter could then choose an API from the model name, which made
proxy names and local model names hard to predict.

The desired flow is explicit.

```text
provider API ────────────┐
                        ├──► resolved model API ──► request endpoint
model API override ─────┘

model ID ───────────────────► request payload
```

These examples show the expected result.

```text
company/openai/gpt-5.6 with openai-responses
    └──► POST /responses

gpt-5.5-proxy with openai-completions
    └──► POST /chat/completions
```

## Pi mapping

Pi stores `api` on each resolved model. Custom-model composition checks the
model setting first and then the provider setting. Streaming dispatch uses the
resolved value.

The implementation should use Pi v0.74.0 at `1eee081` as its reference.

| Pi concept | Tau equivalent | Behavior |
| --- | --- | --- |
| Resolved `Model.api` | `provider_api()` and `OpenAICompatibleConfig.api` | One API is resolved before streaming |
| Provider `api` | `ProviderCatalogEntry.api` and `OpenAICompatibleProviderConfig.api` | Default API for the provider |
| Model `api` | `ModelCatalogMetadata.api` and `ProviderModelMetadata.api` | Override for one model |
| `getApiProvider(model.api)` | Exact branch inside `OpenAICompatibleProvider` | Select the matching endpoint |
| `models.json` | `~/.tau/catalog.toml` | Store provider and model capabilities |

Relevant Pi sources include

- `packages/ai/src/types.ts`
- `packages/coding-agent/src/core/model-config.ts`
- `packages/coding-agent/src/core/provider-composer.ts`
- `packages/coding-agent/docs/models.md`

Tau uses one Python provider class with two internal transports. Pi registers
separate API implementations. Both designs select the transport from the
resolved API value.

## Architecture

The existing dependency direction stays in place.

```text
tau_coding  ─────►  tau_agent  ─────►  tau_ai
 application        portable brain     provider streams
```

This feature touches the application and provider layers.

```text
tau_coding
├── `tau setup`
├── `/login custom`
├── catalog composition
└── runtime provider configuration
          │
          ▼
tau_ai.OpenAICompatibleProvider
├── openai-completions ──► /chat/completions
└── openai-responses ────► /responses
```

`tau_agent`, the harness, tools, events, and session storage keep their current
interfaces. Provider capability metadata remains in `catalog.toml`.

## Implementation

### 1. Route from the configured API

Update `src/tau_ai/openai_compatible.py` so
`OpenAICompatibleProvider._stream_provider_events()` branches only on
`self._config.api`.

```python
if self._config.api == "openai-responses":
    return self._stream_responses(...)
return self._stream_chat_completions(...)
```

Remove the model-name routing helpers and prefixes. Remove
`infer_api_from_model` from `OpenAICompatibleConfig` in `src/tau_ai/env.py`.

Remove the obsolete flag from dynamic providers and llama.cpp in

- `src/tau_coding/provider_runtime.py`
- `src/tau_coding/extensions/builtins/llama_cpp/service.py`

Both paths already configure `openai-completions` explicitly.

### 2. Resolve model and provider metadata in one place

Promote `_provider_api()` in `src/tau_coding/provider_config.py` to the public
application helper `provider_api()`.

The resolver follows this order.

```text
model metadata API
        │
        ▼
provider API
        │
        ▼
openai-completions compatibility value
```

Use the helper when building `OpenAICompatibleConfig`. Keep this policy in
`tau_coding`, where provider catalogs and model metadata are composed.

Leave session and RPC code unchanged. Their existing durable-provider
projection already applies model metadata before provider metadata.

### 3. Add the CLI choice

Update `src/tau_coding/cli.py` with an `--api` option for `tau setup`.

Accepted values include

- `openai-completions`
- `openai-responses`

Use `openai-completions` when the option is omitted. Save the chosen value into
the existing provider definition in `catalog.toml` and preserve its display and
documentation metadata. Save provider preferences through the existing
`providers.json` path.

The CLI save path must materialize the API field when updating an older catalog
entry that lacks it.

### 4. Add the TUI choice

Update `src/tau_coding/tui/app.py` and `CustomProviderLoginResult` with the
selected API.

Place a selector after the base URL with these labels.

- OpenAI Chat Completions (`/chat/completions`)
- OpenAI Responses (`/responses`)

Use Chat Completions as the initial value. Enter should accept the current
value. Space should open the choices. Selecting another value should advance to
the API-key environment field. The implementation should use Textual's public
bindings and messages.

Save the value into both runtime configuration and `ProviderCatalogEntry`.

### 5. Preserve configuration ownership

Continue using the existing persistence split.

```text
catalog.toml   provider and model capabilities, including API
providers.json provider preferences and ordering
session JSONL  selected provider, model, and messages
```

Older OpenAI-compatible catalog entries may omit `api`. Resolve those entries
to `openai-completions` so existing custom providers continue to work. New
entries created through either setup flow always save an explicit value.

## Tests

### Provider routing

Update `tests/test_tau_ai.py` with misleading model names in both directions.

| Configured API | Model ID | Endpoint |
| --- | --- | --- |
| `openai-completions` | `gpt-5.5-proxy` | `/chat/completions` |
| `openai-responses` | `company/openai/gpt-5.6` | `/responses` |

Keep request-shape and stream-parser tests for both APIs. Give each Responses
test an explicit `api="openai-responses"` configuration.

### Catalog composition

Cover these behaviors in `tests/test_provider_config.py` and
`tests/test_provider_catalog.py`.

- Model metadata overrides provider metadata
- Provider and model API values survive catalog round trips
- API-less OpenAI-compatible entries resolve to Chat Completions
- Provider preferences stay free of capability metadata

### CLI and TUI setup

Cover these behaviors in `tests/test_cli.py` and `tests/test_tui_app.py`.

- Both API choices save and reload correctly
- The CLI default saves Chat Completions
- Updating an API-less entry materializes the chosen API
- Invalid CLI values fail before files are written
- TUI focus moves through the selector with the keyboard
- Accepting the initial TUI value works with Enter
- The saved catalog includes the chosen API

Keep dynamic-provider and llama.cpp regression tests focused on explicit Chat
Completions routing.

## Documentation

Update the user-facing provider and configuration guides in

- `website/content/guides/providers-and-models.md`
- `website/content/reference/configuration.md`
- `src/tau_coding/data/docs/models.md`
- `src/tau_coding/data/docs/cli.md`

Explain how users choose an API, where Tau saves it, how model overrides work,
and how older entries behave. Keep Pi-specific implementation detail in
`dev-notes/openai-compatible-protocol-selection.md`.

## Validation

Run the focused suite.

```bash
uv run pytest \
  tests/test_tau_ai.py \
  tests/test_provider_config.py \
  tests/test_provider_catalog.py \
  tests/test_provider_runtime.py \
  tests/test_extension_providers.py \
  tests/test_llama_cpp_extension.py \
  tests/test_cli.py \
  tests/test_tui_app.py
```

Run the repository checks.

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Complete two manual checks against a mock or compatible server.

1. Configure `company/openai/gpt-5.6` with `openai-responses` and confirm a
   request to `/responses`.
2. Configure `gpt-5.5-proxy` with `openai-completions` and confirm a request to
   `/chat/completions`.

## Change surface

Expected source changes include

- `src/tau_ai/env.py`
- `src/tau_ai/openai_compatible.py`
- `src/tau_coding/cli.py`
- `src/tau_coding/provider_config.py`
- `src/tau_coding/provider_runtime.py`
- `src/tau_coding/tui/app.py`
- `src/tau_coding/extensions/builtins/llama_cpp/service.py`

Expected test and documentation changes stay within the files listed above.
The implementation adds no provider, session format, dependency, or framework.
